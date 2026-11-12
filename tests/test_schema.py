"""src/schema.py 단위 테스트 (TASKS A2). 교육용 더미 데이터입니다."""

import pytest
from pydantic import ValidationError

from src.schema import Review

FINDING = {
    "issue_type": "bug",
    "file": "app/chain.py",
    "line": 12,
    "severity": "high",
    "message": "langchain.chains는 1.x에서 제거되어 ImportError가 납니다.",
    "evidence": [
        {
            "type": "doc",
            "source": "data/corpus/deprecated_map.md",
            "rule_id": "DEP-LLMChain",
        }
    ],
    "suggestion": "```python\nchain = prompt | model | parser\n```",
}


def _review(summary_lines: int, **finding_overrides) -> dict:
    return {
        "summary": [f"요약 {i}" for i in range(summary_lines)],
        "findings": [{**FINDING, **finding_overrides}],
        "recommendations": ["다른 파일의 구버전 import도 확인하세요."],
    }


def test_valid_review_passes():
    review = Review.model_validate(_review(5))
    assert review.findings[0].evidence[0].rule_id == "DEP-LLMChain"


@pytest.mark.parametrize("lines", [4, 6])
def test_summary_must_be_exactly_five_lines(lines):
    with pytest.raises(ValidationError):
        Review.model_validate(_review(lines))


def test_finding_needs_at_least_one_evidence():
    with pytest.raises(ValidationError):
        Review.model_validate(_review(5, evidence=[]))


def test_issue_type_is_restricted():
    with pytest.raises(ValidationError):
        Review.model_validate(_review(5, issue_type="performance"))
