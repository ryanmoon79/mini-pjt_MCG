"""관측: `run_query`가 돌려주는 `trace`(결정적 이벤트 목록)를 기록합니다 (Day 7 패턴).

- 이벤트마다 종류(`type`)와 시각(`t`, ISO 형식)·시작 후 경과(`elapsed_ms`)를 남깁니다.
  응답 시간 지표는 첫 이벤트와 마지막 이벤트의 경과 차이로 계산합니다.
- 모델이 무슨 말을 했든 이벤트 종류와 필드는 고정입니다. 평가가 이 기록으로
  도구 호출·HITL 상태·가드레일 결과를 확인합니다.
- 이 기록은 `run_query` 결과의 `trace`로 돌려주고,
  평가 실행기가 `trace.json`으로 저장합니다.
- `.env`에 `LANGSMITH_TRACING=true`·`LANGSMITH_API_KEY`가 있으면
  LangChain이 LangSmith로도 보냅니다.
- trace에는 마스킹 후 내용만 넣습니다 (호출하는 쪽이 마스킹된 값을 넘김).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

# 고정 이벤트 종류
EVENT_TYPES = {
    "start",  # 리뷰 시작 (입력 경로)
    "node",  # 그래프 노드 진입
    "guardrail",  # 입력·출력 가드레일 결과
    "tool",  # 도구 호출 (이름·인자)
    "plan",  # planner 결과 (리뷰어·파일 범위)
    "reviewer",  # 리뷰어 하나의 결과 (지적 수)
    "review",  # 최종 Review JSON
    "hitl",  # 승인 대기 상태·thread_id
    "post",  # 게시(dry-run) 결과
    "usage",  # 역할별 모델 호출 수·입력·출력 토큰 (UsageRecorder)
    "error",  # 처리 중 오류
    "end",  # 리뷰 끝
}


class TraceRecorder:
    """리뷰 한 건의 이벤트를 순서대로 모읍니다."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self.events: list[dict] = []

    def add(self, type_: str, **fields) -> dict:
        """이벤트 하나를 기록하고 돌려줍니다. 알 수 없는 종류는 ValueError."""
        if type_ not in EVENT_TYPES:
            raise ValueError(f"알 수 없는 trace 이벤트 종류: {type_}")
        event = {
            "type": type_,
            "t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "elapsed_ms": round((time.perf_counter() - self._start) * 1000),
            **fields,
        }
        self.events.append(event)
        return event

    def tool_calls(self) -> list[dict]:
        """도구 호출 이벤트만 (케이스 통과 판정의 expected_tools 확인용)."""
        return [e for e in self.events if e["type"] == "tool"]


def duration_ms(events: list[dict]) -> int:
    """trace의 첫 이벤트부터 마지막 이벤트까지 걸린 시간(ms)."""
    if not events:
        return 0
    return events[-1]["elapsed_ms"] - events[0]["elapsed_ms"]


# ── 토큰 사용량: 역할별 모델 호출 수·입력·출력 토큰 ─────────────────────

# 모델을 부르는 쪽이 config metadata에 이 키로 역할 이름을 넣습니다
# (예: "bug_reviewer", "bug_reviewer:finalize", "synthesize:retry", "suggestion_fix").
# 넣지 않은 호출은 "기타"로 셉니다 (숫자를 지어내지 않음).
ROLE_KEY = "citereview_role"
UNKNOWN_ROLE = "기타"


class UsageRecorder:
    """모델 호출마다 metadata의 역할 이름으로 토큰을 모으는 콜백 (스레드 안전).

    `langchain_core`의 configure hook으로 붙으므로 그래프 안 어느 깊이의 호출이든
    잡힙니다. 토큰 값은 모델이 돌려준 `usage_metadata`를 그대로 씁니다.
    """

    def __init__(self) -> None:
        import threading

        from langchain_core.callbacks import BaseCallbackHandler

        recorder = self

        class _Handler(BaseCallbackHandler):
            def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
                recorder._start(run_id, kw.get("metadata"))

            def on_llm_start(self, serialized, prompts, *, run_id, **kw):
                recorder._start(run_id, kw.get("metadata"))

            def on_llm_end(self, response, *, run_id, **kw):
                recorder._end(run_id, response)

            def on_llm_error(self, error, *, run_id, **kw):
                recorder._roles.pop(run_id, None)

        self.handler = _Handler()
        self._lock = threading.Lock()
        self._roles: dict = {}
        self.by_role: dict[str, dict[str, int]] = {}

    def _start(self, run_id, metadata) -> None:
        with self._lock:
            self._roles[run_id] = (metadata or {}).get(ROLE_KEY, UNKNOWN_ROLE)

    def _end(self, run_id, response) -> None:
        usage = {}
        for gens in response.generations:
            for g in gens:
                message = getattr(g, "message", None)
                usage = getattr(message, "usage_metadata", None) or usage
        with self._lock:
            role = self._roles.pop(run_id, UNKNOWN_ROLE)
            row = self.by_role.setdefault(
                role, {"calls": 0, "input_tokens": 0, "output_tokens": 0}
            )
            row["calls"] += 1
            row["input_tokens"] += int(usage.get("input_tokens") or 0)
            row["output_tokens"] += int(usage.get("output_tokens") or 0)

    def summary(self) -> dict:
        """trace `usage` 이벤트 필드: 역할별 값과 합계."""
        with self._lock:
            by_role = {k: dict(v) for k, v in sorted(self.by_role.items())}
        total = {
            k: sum(r[k] for r in by_role.values())
            for k in ("calls", "input_tokens", "output_tokens")
        }
        return {"by_role": by_role, "total": total}


_USAGE_VAR = None


def record_usage():
    """이 컨텍스트 안의 모든 모델 호출 토큰을 역할별로 모으는 컨텍스트 관리자.

    사용: `with record_usage() as usage: graph.invoke(...)` 뒤 `usage.summary()`.
    """
    from contextlib import contextmanager
    from contextvars import ContextVar

    from langchain_core.tracers.context import register_configure_hook

    global _USAGE_VAR
    if _USAGE_VAR is None:  # 훅은 프로세스에서 한 번만 등록
        _USAGE_VAR = ContextVar("citereview_usage", default=None)
        register_configure_hook(_USAGE_VAR, inheritable=True)

    @contextmanager
    def _scope():
        recorder = UsageRecorder()
        token = _USAGE_VAR.set(recorder.handler)
        try:
            yield recorder
        finally:
            _USAGE_VAR.reset(token)

    return _scope()
