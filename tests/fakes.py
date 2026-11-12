"""테스트용 가짜 모델 (실제 LLM 호출 없음).

`GenericFakeChatModel`은 `bind_tools`가 없어 supervisor·create_agent·구조화 출력에서
쓸 수 없으므로, `bind_tools`를 지원하고 받은 메시지를 기록하는 하위 클래스를 씁니다.
"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from pydantic import Field


class ToolFake(GenericFakeChatModel):
    """도구 바인딩을 받아들이고, 모델에 들어온 메시지를 `seen`에 기록하는 가짜 모델."""

    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append([m.content for m in messages])
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def fake(*replies) -> ToolFake:
    """문자열은 일반 답, dict 목록은 tool_calls로 바꿔 순서대로 답하는 가짜 모델."""
    msgs = []
    for i, r in enumerate(replies):
        if isinstance(r, str):
            msgs.append(AIMessage(content=r))
        else:
            calls = [
                {"name": c["name"], "args": c["args"], "id": f"call_{i}_{j}"}
                for j, c in enumerate(r)
            ]
            msgs.append(AIMessage(content="", tool_calls=calls))
    return ToolFake(messages=iter(msgs))


def call(name: str, **args) -> list[dict]:
    """tool_calls 하나짜리 목록."""
    return [{"name": name, "args": args}]


def handoff(agent: str) -> list[dict]:
    """supervisor가 리뷰어에게 넘기는 tool_call."""
    return call(f"transfer_to_{agent}")
