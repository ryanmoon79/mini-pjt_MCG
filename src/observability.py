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
