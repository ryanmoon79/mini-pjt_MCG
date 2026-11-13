"""로컬 API 서버: `POST /query`로 `run_query` 결과(answer·contexts·trace)를 돌려줍니다.

    python -m src.interfaces.api_server [--port 8766]

    curl -X POST http://127.0.0.1:8766/query -H "Content-Type: application/json" \\
         -d "{\\"question\\": \\"data/eval_diffs/03_llmchain.diff 를 리뷰해줘\\"}"

- 요청: `{"question": "<diff 경로를 담은 문장>"}` — CSV `input`·CLI와 같은 형식.
- 응답: `run_query` 결과 그대로(`answer`·`contexts`·`trace`, 과정 공통 규약 3-1번).
- **HITL:** 리뷰는 승인 대기(`trace`의 `hitl` 이벤트 `status="waiting"`)에서 멈추고,
  이 서버는 재개·게시를 하지 않습니다(`--batch`·평가 실행기와 같은 동작).
  게시가 필요하면 CLI 대화형이나 GUI를 씁니다.
- 표준 라이브러리(`http.server`)만 씁니다. **127.0.0.1에서만** 열고, GUI 서버와 같이
  Host 헤더가 이 서버 자신(`127.0.0.1:포트`·`localhost:포트`)일 때만 처리합니다
  (DNS 리바인딩 방어). 브라우저가 붙이는 Origin 헤더가 있으면 이 서버 자신일 때만
  받습니다(다른 사이트의 요청 위조 방어). curl처럼 Origin이 없는 로컬 호출은 받습니다.
- Windows에서 이미 쓰는 포트에 서버가 하나 더 뜨지 않게 주소 재사용을 끕니다
  (GUI 서버에서 두 서버가 같은 포트를 잡아 옛 코드가 요청을 받은 일이 있었음).
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.review.graph import run_query

HOST = "127.0.0.1"
DEFAULT_PORT = 8766
MAX_BODY = 64 * 1024  # 요청 본문 상한 (질문 문장만 받으므로 충분)


class ExclusiveServer(ThreadingHTTPServer):
    """이미 쓰는 포트면 시작에 실패하는 서버 (Windows SO_REUSEADDR 중복 바인딩 방지)."""

    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    """`POST /query`(리뷰)와 `GET /health`(상태)만 받습니다."""

    def log_message(self, fmt, *args):  # 요청 로그는 조용히
        return

    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _own_hosts(self) -> set[str]:
        """이 서버 자신의 `호스트:포트` (127.0.0.1과 localhost만)."""
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _allowed(self) -> bool:
        """Host가 이 서버 자신이고, Origin이 있으면 그것도 이 서버 자신일 때만 허용."""
        if (self.headers.get("Host") or "").lower() not in self._own_hosts():
            return False
        origin = (self.headers.get("Origin") or "").lower()
        return not origin or origin in {f"http://{h}" for h in self._own_hosts()}

    def do_GET(self):  # noqa: N802 (http.server 규약)
        if not self._allowed():
            return self._json(
                {"error": "허용되지 않은 요청입니다 (Host 또는 Origin)."}, 403
            )
        if self.path == "/health":
            return self._json({"status": "ok"})
        return self._json({"error": "POST /query 또는 GET /health만 있습니다."}, 404)

    def do_POST(self):  # noqa: N802 (http.server 규약)
        if not self._allowed():
            return self._json(
                {"error": "허용되지 않은 요청입니다 (Host 또는 Origin)."}, 403
            )
        if self.path != "/query":
            return self._json({"error": "POST /query만 있습니다."}, 404)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json({"error": "요청 본문이 너무 큽니다."}, 413)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "JSON 본문이 아닙니다."}, 400)
        question = data.get("question") if isinstance(data, dict) else None
        if not isinstance(question, str) or not question.strip():
            return self._json(
                {"error": '{"question": "..."} 형식으로 보내 주세요.'}, 400
            )
        try:
            result = run_query(question)
        except Exception as exc:  # diff 경로 없음 등은 400, 서버는 계속 동작
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)
        return self._json(result)


def main(argv: list[str] | None = None) -> int:
    """API 서버를 127.0.0.1에서 엽니다 (Ctrl+C로 종료)."""
    parser = argparse.ArgumentParser(description="CiteReview 로컬 API (POST /query)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        server = ExclusiveServer((HOST, args.port), Handler)
    except OSError as exc:
        print(f"{HOST}:{args.port}를 열 수 없습니다 (이미 사용 중일 수 있음): {exc}")
        return 1
    print(f"CiteReview API: http://{HOST}:{args.port}/query (Ctrl+C로 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
