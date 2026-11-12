"""src/interfaces/gui_server.py 요청 검사 테스트 (CLAUDE.md 6번 "GUI").

서버를 이 프로세스 안에서 임의 포트로 띄우고 `http.client`로 요청합니다.
실제 LLM이나 리뷰는 실행하지 않습니다 (거절되어야 하는 요청만 보내고,
리뷰 시작 함수는 가짜로 바꿔 호출 여부만 확인).
"""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from src.interfaces import gui_server


@pytest.fixture
def server(monkeypatch):
    started = []
    monkeypatch.setattr(
        gui_server, "start_job", lambda path, **kw: started.append(path)
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), gui_server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], started
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path, headers, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    data = json.dumps(body).encode() if body is not None else b""
    for k, v in {**headers, "Content-Length": str(len(data))}.items():
        conn.putheader(k, v)
    conn.endheaders(data)
    resp = conn.getresponse()
    status = resp.status
    resp.read()
    conn.close()
    return status


def test_other_host_is_rejected(server):
    """Host 헤더가 이 서버(127.0.0.1·localhost:포트)가 아니면 GET도 403."""
    port, _ = server
    assert _request(port, "GET", "/", {"Host": f"127.0.0.1:{port}"}) == 200
    assert _request(port, "GET", "/", {"Host": f"localhost:{port}"}) == 200
    assert _request(port, "GET", "/", {"Host": "evil.example:80"}) == 403
    assert _request(port, "GET", "/api/diffs", {"Host": f"evil.example:{port}"}) == 403


def test_other_origin_is_rejected(server):
    """상태를 바꾸는 POST는 Origin이 이 서버가 아니거나 없으면 403 (리뷰 미시작)."""
    port, started = server
    host = {"Host": f"127.0.0.1:{port}"}
    body = {"mode": "file", "path": "data/eval_diffs/03_llmchain.diff"}
    for origin in ("http://evil.example", f"http://127.0.0.1:{port + 1}", None):
        headers = {**host, **({"Origin": origin} if origin else {})}
        assert _request(port, "POST", "/api/review", headers, body) == 403
        assert _request(port, "POST", "/api/pick-folder", headers, {}) == 403
        assert _request(port, "POST", "/api/decision", headers, {"job": "x"}) == 403
    assert started == []
    # 같은 출처의 Origin이면 통과 (리뷰 시작 함수까지 도달)
    ok = {**host, "Origin": f"http://127.0.0.1:{port}"}
    assert _request(port, "POST", "/api/review", ok, body) == 200
    assert len(started) == 1


def test_job_progress_copies_node_events_from_review_thread(monkeypatch, tmp_path):
    """리뷰 스레드의 trace 노드 진입 이벤트가 그 작업의 진행 단계로 복사된다.

    가짜 모델로 run_query를 감싸 실제 LLM 없이 start_job → 폴링 → 승인 대기까지 본다.
    """
    import time

    from langchain_core.embeddings import DeterministicFakeEmbedding

    from src.evidence.search import build_retriever
    from src.observability.trace import TraceRecorder
    from src.review.graph import run_query
    from tests.fakes import call, fake, handoff

    retriever = build_retriever(
        embeddings=DeterministicFakeEmbedding(size=32),
        persist_directory=str(tmp_path / "chroma"),
        collection_name="test_gui_progress",
    )
    models = {
        "supervisor": fake(handoff("bug_reviewer"), handoff("style_reviewer"), "끝"),
        "bug_reviewer": fake(call("ReviewerFindings", findings=[])),
        "style_reviewer": fake(call("ReviewerFindings", findings=[])),
        "synthesizer": fake(
            call(
                "SummaryDraft",
                change="app/chain.py에 요약 체인을 추가합니다.",
                action="꼭 고쳐야 할 것은 없습니다.",
                recommendations=[],
            )
        ),
    }
    # 훅이 바꾼 TraceRecorder.add를 테스트 뒤에 되돌림
    monkeypatch.setattr(TraceRecorder, "add", TraceRecorder.add)
    monkeypatch.setattr(
        gui_server,
        "run_query",
        lambda q, **kw: run_query(q, llm=models, retriever=retriever, **kw),
    )
    gui_server._install_progress_hook()
    diff = gui_server.config.EVAL_DIFFS_DIR / "03_llmchain.diff"
    job_id = gui_server.start_job(diff)
    deadline = time.time() + 60
    while gui_server.job_view(job_id)["status"] == "running":
        assert time.time() < deadline, "리뷰 스레드가 끝나지 않음"
        time.sleep(0.05)
    view = gui_server.job_view(job_id)
    assert view["status"] == "waiting" and view["thread_id"]
    assert all(step["done"] for step in view["steps"]), view["steps"]
    names = [e["name"] for e in gui_server.JOBS[job_id]["events"]]
    assert names[0] == "input_guardrail" and "synthesize" in names
