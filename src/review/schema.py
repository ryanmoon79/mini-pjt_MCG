"""리뷰 흐름에서만 쓰는 구조화 출력: 리뷰어 결과, 종합 노드 초안, 고치는 방법 재생성.

여러 도메인이 함께 쓰는 지적 데이터 모델(Evidence·Finding·Review)은
src/findings/schema.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.findings.schema import Finding


class ReviewerFindings(BaseModel):
    """리뷰어 에이전트 하나의 구조화 출력 (지적 목록만)."""

    findings: list[Finding] = Field(
        default_factory=list, description="이 리뷰어가 찾은 지적. 없으면 빈 목록"
    )


class SuggestionFix(BaseModel):
    """출력 검증이 고치는 방법을 다시 만들게 할 때의 구조화 출력."""

    message: str = Field(
        description="무엇이 문제이고 왜 문제인지 한두 문장 (langchain_classic 금지)"
    )
    suggestion: str = Field(
        description="1.x API만 쓴 고치는 방법. 코드는 ```python 블록에 고친 줄만"
    )


class SummaryDraft(BaseModel):
    """종합 노드(LLM)의 구조화 출력: 요약 5줄 중 LLM이 쓰는 두 줄 + 추천 개선안.

    요약 2·3·4줄(보안·버그 결과, 스타일 결과, 리뷰 범위·기준)은 코드가 지적 목록과
    설정에서 만듭니다. LLM은 사실 확인이 diff로만 가능한 두 줄만 씁니다.
    """

    change: str = Field(
        min_length=1,
        description="1줄: 변경 내용 요약. diff로 확인되는 사실만, 추측·평가 금지",
    )
    action: str = Field(
        min_length=1,
        description="5줄: 가장 먼저 할 일. 지적 목록 범위 안에서만, 평가성 문장 금지",
    )
    recommendations: list[str] = Field(
        default_factory=list, description="리뷰 전체에 대한 추천 개선안 1~3개"
    )
