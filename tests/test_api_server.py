"""src/interfaces/api_server.py 테스트 (POST /query, 가짜 모델 — 실제 LLM 호출 없음).

서버를 이 프로세스 안에서 임의 포트로 띄우고 `http.client`로 요청합니다.
"""

import http.client
import json
import threading

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from src.evidence.search import build_retriever
from src.interfaces import api_server
from src.review.graph import run_query
from tests.fakes import call, fake, handoff


def _models():
    """버그 리뷰어가 03번 diff 12행에 지적 하나를 내는 가짜 모델 묶음."""
    bug = {
        "issue_type": "bug",
        "file": "app/chain.py",
        "line": 12,
        "severity": "high",
        "message": "LLMChain은 1.x에서 제거되어 ImportError가 납니다.",
        "evidence": [{"type": "code", "source": "app/chain.py:12"}],
        "suggestion": "LCEL로 바꿉니다.",
    }
    return {
        "supervisor": fake(handoff("bug_reviewer"), handoff("style_reviewer"), "끝"),
        "bug_reviewer": fake(call("ReviewerFindings", findings=[bug])),
        "style_reviewer": fake(call("ReviewerFindings", findings=[])),
        "synthesizer": fake(
            call(
                "SummaryDraft",
                change="app/chain.py에 요약 체인을 추가합니다.",
                action="먼저 app/chain.py 12행의 버그 지적을 고치세요.",
                recommendations=["LCEL로 바꾸세요."],
            )
        ),
    }


@pytest.fixture
def server(monkeypatch, tmp_path):
    retriever = build_retriever(
        embeddings=DeterministicFakeEmbedding(size=32),
        persist_directory=str(tmp_path / "chroma"),
        collection_name="test_api_server",
    )
    monkeypatch.setattr(
        api_server,
        "run_query",
        lambda q: run_query(q, llm=_models(), retriever=retriever),
    )
    httpd = api_server.ExclusiveServer(("127.0.0.1", 0), api_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.putrequest(method, path, skip_host=True)
    data = json.dumps(body).encode() if body is not None else b""
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    for k, v in {**hdrs, "Content-Length": str(len(data))}.items():
        conn.putheader(k, v)
    conn.endheaders(data)
    resp = conn.getresponse()
    status, payload = resp.status, json.loads(resp.read() or b"{}")
    conn.close()
    return status, payload


def test_post_query_returns_run_query_result_and_stops_at_hitl(server):
    """응답은 run_query 결과 그대로(answer·contexts·trace), 승인 대기에서 멈춤.

    게시 이벤트와 github_post_comment 호출은 없다.
    """
    status, out = _request(
        server,
        "POST",
        "/query",
        {"question": "data/eval_diffs/03_llmchain.diff 를 리뷰해줘"},
    )
    assert status == 200
    assert set(out) == {"answer", "contexts", "trace"}
    assert out["answer"].startswith("CiteReview · data/eval_diffs/03_llmchain.diff")
    hitl = [e for e in out["trace"] if e["type"] == "hitl"]
    assert [e["status"] for e in hitl] == ["waiting"] and hitl[0]["thread_id"]
    assert not [e for e in out["trace"] if e["type"] == "post"]
    assert not [
        e
        for e in out["trace"]
        if e["type"] == "tool" and e["name"] == "github_post_comment"
    ]


def test_api_rejects_foreign_host_origin_and_bad_body(server):
    """다른 Host·다른 사이트 Origin은 403, 형식이 틀린 본문은 400, 다른 경로는 404."""
    q = {"question": "data/eval_diffs/03_llmchain.diff 를 리뷰해줘"}
    assert _request(server, "POST", "/query", q, {"Host": "evil.example:80"})[0] == 403
    origin = {"Origin": "http://evil.example"}
    assert _request(server, "POST", "/query", q, origin)[0] == 403
    assert _request(server, "POST", "/query", {"q": "x"})[0] == 400
    assert _request(server, "POST", "/other", q)[0] == 404
    assert _request(server, "GET", "/health") == (200, {"status": "ok"})
