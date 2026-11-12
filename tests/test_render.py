"""src/render.py 형식 테스트 (TASKS A5).

03번 평가 diff(교육용 더미 데이터)로 렌더링한 결과의 **구조**가 기준 화면
docs/output_example.md와 같은지 비교합니다. 내용(지적 수·문장)은 다르므로
줄마다 "종류"로 바꾼 뼈대를 비교합니다. LLM은 쓰지 않습니다.
"""

import re

from src import config
from src import diff_parser as dp
from src.render import render_review
from src.schema import Review

EXAMPLE = config.ROOT_DIR / "docs" / "output_example.md"

KINDS = [
    ("TITLE", r"^CiteReview · "),
    ("INFO", r"^리뷰: .+ · 건너뜀: .+ · 마스킹: "),
    ("MUST", r"^꼭 고쳐야 할 것 "),
    ("H2", r"^## "),
    ("H3", r"^### "),
    ("ITEM", r"^\d+\. \S+:\d+ · (high|medium|low)$"),
    ("CODE", r"^ {4}[> ] +\d+ \| "),
    ("EVID", r"^ {3}근거: .+ \(.+\)"),
    ("NONE", r"^문제를 찾지 못했습니다\.$"),
    ("EMPTY", r"^\(없음\)$"),
    ("FIX", r"^ {3}고치는 방법:"),
    ("MINUS", r"^ {5}- "),
    ("PLUS", r"^ {5}\+ "),
    (
        "FOOT",
        r"^지적 \d+건 \(꼭 고칠 것 \d+ · 다듬을 것 \d+\) · "
        r"근거가 확인되지 않아 제외된 지적 \d+건 \(--verbose로 확인\)$",
    ),
]


def skeleton(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        for kind, pattern in KINDS:
            if re.match(pattern, line):
                out.append(line if kind in {"H2", "H3"} else kind)
                break
    return out


def example_text(index: int = 0) -> str:
    """기준 화면의 index번째 코드 블록 (0: 지적 있는 예시, 1: 문제없는 diff 예시)."""
    body = EXAMPLE.read_text(encoding="utf-8")
    return re.findall(r"```\n(.*?)```", body, re.DOTALL)[index]


def rendered_03() -> str:
    files = dp.parse_diff(
        (config.EVAL_DIFFS_DIR / "03_llmchain.diff").read_text(encoding="utf-8")
    )
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[
            {
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
                "suggestion": "```python\nfrom langchain_core.output_parsers import "
                "StrOutputParser\n```\n체인은 LCEL로 만듭니다.",
            },
            {
                "issue_type": "style",
                "file": "app/chain.py",
                "line": 22,
                "severity": "low",
                "message": "반환 타입이 제거된 클래스입니다.",
                "evidence": [{"type": "code", "source": "app/chain.py:22"}],
                "suggestion": "반환 타입을 Runnable로 바꿉니다.",
            },
        ],
        recommendations=["다른 파일의 구버전 import도 확인하세요."],
    )
    return render_review(
        review,
        files,
        title="data/eval_diffs/03_llmchain.diff",
        doc_titles={"DEP-LLMChain": "deprecated API 매핑표 — LLMChain"},
        dropped=1,
    )


def test_section_headings_match_example():
    headings = [k for k in skeleton(example_text()) if k.startswith("#")]
    assert [k for k in skeleton(rendered_03()) if k.startswith("#")] == headings


def test_top_and_footer_lines_match_example():
    for text in (example_text(), rendered_03()):
        sk = skeleton(text)
        assert sk[:3] == ["TITLE", "INFO", "MUST"]
        assert sk[-1] == "FOOT"


def test_finding_block_has_same_parts_in_same_order():
    """지적 하나 = 번호 줄 → 코드 발췌(±1줄) → 근거 → 고치는 방법(-/+)."""
    sk = skeleton(rendered_03())
    i = sk.index("ITEM")
    assert sk[i : i + 7] == ["ITEM", "CODE", "CODE", "CODE", "EVID", "FIX", "MINUS"]
    assert sk[i + 7] == "PLUS"
    text = rendered_03()
    assert "> 12 | from langchain.chains import LLMChain" in text
    assert "근거: deprecated API 매핑표 — LLMChain (DEP-LLMChain)" in text
    assert "꼭 고쳐야 할 것 1건 (버그 1)" in text
    assert "제외된 지적 1건" in text


def rendered_clean() -> str:
    files = dp.parse_diff(
        (config.EVAL_DIFFS_DIR / "10_clean_refactor.diff").read_text(encoding="utf-8")
    )
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[],
        recommendations=["테스트를 추가하세요."],
    )
    return render_review(review, files, title="data/eval_diffs/10_clean_refactor.diff")


def test_clean_diff_matches_example():
    """문제없는 diff: 안내 줄 다음 "문제를 찾지 못했습니다." + 빈 소제목 "(없음)"."""
    assert skeleton(rendered_clean()) == skeleton(example_text(1))
    assert skeleton(rendered_clean())[:4] == ["TITLE", "INFO", "MUST", "NONE"]
    assert "NONE" not in skeleton(rendered_03())  # 지적이 있으면 표시하지 않음


def test_long_fix_is_truncated_to_five_lines():
    files = dp.parse_diff(
        (config.EVAL_DIFFS_DIR / "03_llmchain.diff").read_text(encoding="utf-8")
    )
    code = "\n".join(f"line_{i} = {i}" for i in range(8))
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[
            {
                "issue_type": "bug",
                "file": "app/chain.py",
                "line": 12,
                "severity": "high",
                "message": "m",
                "evidence": [{"type": "code", "source": "app/chain.py:12"}],
                "suggestion": f"```python\n{code}\n```",
            }
        ],
    )
    text = render_review(review, files, title="t")
    assert sum(1 for ln in text.splitlines() if ln.startswith("     + ")) == 5
    assert "…(전체는 --verbose)" in text
