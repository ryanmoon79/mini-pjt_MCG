"""CiteReview 출력 스키마 (단일 기준).

리뷰어의 구조화 출력, 출력 가드레일의 형식 검사, 평가 스크립트가 모두 이 모델을 씁니다.
- `issue_type` 분류 규칙은 SERVICE.md 1번을 따릅니다 (CSV의 `category`와 다른 개념).
- `rule_id`는 CLAUDE.md 7번의 근거 규칙 ID 체계만 씁니다.
- 근거의 *형식*은 여기서, 근거의 *실재*(이번 diff·검색·도구 출력에 있었는지)는
  출력 검증 노드에서 확인합니다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

SUMMARY_LINES = 5  # 요약은 정확히 5줄 (SERVICE.md 4번 정책 4)


def check_summary(value: list[str]) -> list[str]:
    """요약이 정확히 5줄이고 빈 줄이 없는지 확인합니다 (Review)."""
    if len(value) != SUMMARY_LINES:
        raise ValueError(
            f"요약은 정확히 {SUMMARY_LINES}줄이어야 합니다 (받은 줄 수: {len(value)})"
        )
    if any(not line.strip() for line in value):
        raise ValueError("요약에 빈 줄이 있습니다")
    return value


class Evidence(BaseModel):
    """지적 하나를 뒷받침하는 근거 하나."""

    type: Literal["doc", "tool", "code"] = Field(
        description="근거 종류: doc=문서 청크, tool=도구 출력, code=diff 코드 위치"
    )
    source: str = Field(
        description='문서 경로, 도구 이름("ruff", "secret-scanner"), 또는 "파일:라인"'
    )
    rule_id: str | None = Field(
        default=None,
        description='규칙 ID. 예: "PEP8-imports", "DEP-LLMChain", "ruff:F401", '
        '"secret-scanner:aws-access-key". code 근거는 보통 None',
    )


class Finding(BaseModel):
    """리뷰 지적 하나."""

    issue_type: Literal["bug", "style", "security"] = Field(
        description=(
            "bug=동작이 틀려짐, security=보안 위험, style=동작은 같지만 규약과 다름"
        )
    )
    file: str = Field(description="diff 안의 파일 경로 (변경 후 기준)")
    line: int = Field(ge=1, description="변경 후 파일 기준 줄 번호")
    severity: Literal["high", "medium", "low"]
    message: str = Field(description="무엇이 문제이고 왜 문제인지 한두 문장")
    evidence: list[Evidence] = Field(min_length=1, description="근거 최소 1개")
    suggestion: str = Field(
        description="고치는 방법. 코드가 있으면 ```python 블록으로 고친 코드를 넣음"
    )


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


class Review(BaseModel):
    """리뷰 전체 결과."""

    summary: list[str] = Field(description="요약, 정확히 5줄")
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[str] = Field(
        default_factory=list, description="리뷰 전체에 대한 추천 개선안"
    )

    @field_validator("summary")
    @classmethod
    def _summary_must_have_five_lines(cls, value: list[str]) -> list[str]:
        """요약이 정확히 5줄이고 빈 줄이 없는지 확인합니다."""
        return check_summary(value)
