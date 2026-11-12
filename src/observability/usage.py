"""토큰 사용량: 모델 호출마다 역할별 호출 수·입력·출력 토큰을 모읍니다.

`run_query`가 이 기록을 trace의 `usage` 이벤트로 남깁니다.
"""

from __future__ import annotations

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
