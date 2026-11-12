"""마스킹 미들웨어: 모든 리뷰어 모델 호출 직전에 메시지를 한 번 더 가립니다.

입력 가드레일 노드의 마스킹과 같은 함수(mask_text)를 써서 이중으로 방어합니다.
"""

from __future__ import annotations

from src.guardrails.input import mask_text

# ── 미들웨어: 모든 리뷰어 모델 호출 직전에 한 번 더 마스킹 (이중 방어) ─────


def _mask_message(message):
    content = message.content
    if isinstance(content, str):
        return message.model_copy(update={"content": mask_text(content)})
    if isinstance(content, list):
        return message.model_copy(update={"content": [_mask_part(p) for p in content]})
    return message


def _mask_part(part):
    """content 블록 하나를 가립니다.

    일반 text 블록과, gpt-oss가 돌려주는 추론 블록
    (`{"type": "reasoning_content", "reasoning_content": {"text": ...}}`)의
    text도 가립니다.
    """
    if not isinstance(part, dict):
        return part
    if isinstance(part.get("text"), str):
        part = {**part, "text": mask_text(part["text"])}
    inner = part.get("reasoning_content")
    if isinstance(inner, dict) and isinstance(inner.get("text"), str):
        masked = {**inner, "text": mask_text(inner["text"])}
        part = {**part, "reasoning_content": masked}
    return part


def masking_middleware():
    """`create_agent`에 붙이는 마스킹 미들웨어 (모델에 가는 메시지를 가림).

    입력 가드레일 노드가 이미 diff를 가렸지만, 도구 출력·이전 메시지 등 모델로 가는
    모든 내용을 모델 호출 직전에 같은 `mask_text`로 한 번 더 가립니다.
    """
    from langchain.agents.middleware import AgentMiddleware

    class MaskingMiddleware(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            masked = mask_messages(request.messages)
            return handler(request.override(messages=masked))

    return MaskingMiddleware()


def mask_messages(messages: list) -> list:
    """메시지 목록 전체를 가립니다 (미들웨어 밖에서 모델을 직접 부를 때도 같은 함수)."""
    return [_mask_message(m) for m in messages]
