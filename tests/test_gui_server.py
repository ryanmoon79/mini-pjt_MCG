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
