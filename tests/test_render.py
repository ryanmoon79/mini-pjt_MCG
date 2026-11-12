"""src/report/render.py 형식 테스트 (TASKS A5).

03번 평가 diff(교육용 더미 데이터)로 렌더링한 결과의 **구조**가 기준 화면
docs/output_example.md와 같은지 비교합니다. 내용(지적 수·문장)은 다르므로
줄마다 "종류"로 바꾼 뼈대를 비교합니다. LLM은 쓰지 않습니다.
"""

import re

from src.diff import parser as dp
from src.findings.schema import Review
from src.report.render import render_review
from src.shared import config

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
            {  # 줄 길이: 번호 항목이 아니라 끝의 "## 참고"로 (기준 화면과 같은 구조)
                "issue_type": "style",
                "file": "app/chain.py",
                "line": 18,
                "severity": "low",
                "message": "줄 길이가 95칸으로 기준 88칸을 넘습니다.",
                "evidence": [
                    {"type": "tool", "source": "ruff", "rule_id": "ruff:E501"}
                ],
                "suggestion": "줄을 나눕니다.",
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
    # 12행(import)을 고친 긴 제안: 첫 낱말 from이 같아 -/+ 표시
    code = "\n".join(f"from langchain_core.x{i} import A{i}" for i in range(8))
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


# ── 2부: 고치는 방법 들여쓰기, 줄 길이(E501) 묶음 ─────────────────────────


def _files_03():
    return dp.parse_diff(
        (config.EVAL_DIFFS_DIR / "03_llmchain.diff").read_text(encoding="utf-8")
    )


def _style(line, rule_ids, suggestion="s"):
    return {
        "issue_type": "style",
        "file": "app/chain.py",
        "line": line,
        "severity": "low",
        "message": "m",
        "evidence": [
            {"type": "tool", "source": "ruff", "rule_id": r} for r in rule_ids
        ],
        "suggestion": suggestion,
    }


def test_fix_plus_lines_take_indent_of_minus_line():
    """+ 줄 전체에 - 줄의 들여쓰기를 붙임 (+ 줄끼리 상대 들여쓰기 유지)."""
    files = _files_03()
    original = dp.new_side_lines(files[0])[24]  # 들여쓰기 4칸인 함수 본문 줄
    assert original.startswith("    ") and not original.startswith("     ")
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[
            {
                "issue_type": "bug",
                "file": "app/chain.py",
                "line": 24,
                "severity": "high",
                "message": "m",
                "evidence": [{"type": "code", "source": "app/chain.py:24"}],
                # 지적한 줄을 고친 제안 (대입 대상 model이 같음)
                "suggestion": "```python\n        model = model_id or DEFAULT_MODEL\n"
                "        if not model:\n            raise ValueError\n```",
            }
        ],
    )
    lines = render_review(review, files, title="t").splitlines()
    assert f"     - {original.rstrip()}" in lines
    # LLM이 준 8칸은 걷어 내고 원래 줄의 4칸
    assert "     +     model = model_id or DEFAULT_MODEL" in lines
    assert "     +     if not model:" in lines
    assert "     +         raise ValueError" in lines  # 상대 들여쓰기(4칸) 유지


def test_line_length_is_a_short_note_not_numbered_items():
    """E501은 번호 항목에서 빠지고 화면 끝 "## 참고" 두 줄로만 (round3 이후 ③).

    줄 번호만 보이고 길이·코드/주석 구분은 --verbose(line_length_detail)에만 있다.
    건수·다듬을 것·HITL 번호에서 빠지며, Review JSON에는 개별 지적이 그대로 남는다.
    """
    from src.report.render import line_length_detail, line_length_of, ordered_items

    files = _files_03()
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[
            _style(23, ["ruff:E501", "PEP8-maximum-line-length"], "```python\nx\n```"),
            _style(24, ["ruff:E501"], "```python\ny\n```"),
            _style(17, ["ruff:E711"]),
        ],
    )
    lengths = {("app/chain.py", 23): (95, 88), ("app/chain.py", 24): (91, 88)}
    text = render_review(review, files, title="t", line_lengths=lengths)
    style = text.split("## 스타일 이슈", 1)[1].split("## 추천 개선안", 1)[0]
    assert "1. app/chain.py:17 · low" in style and "줄 길이" not in style
    note = text.split("## 참고\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert note == [
        "줄 길이 초과 2줄 (기준 88칸): app/chain.py 23, 24"
        " / ruff format 또는 괄호 안 줄바꿈으로 정리",
        "(ruff:E501, PEP8-maximum-line-length, 길이는 --verbose)",
    ]
    assert (
        "(95)" not in text and "주석·docstring" not in text
    )  # 길이·구분은 화면에 없음
    assert "지적 1건 (꼭 고칠 것 0 · 다듬을 것 1)" in text  # E501 제외
    assert [len(it) for it in ordered_items(review)] == [1]  # HITL 번호도 E711만
    assert len(review.findings) == 3  # Review JSON에는 개별 지적이 그대로 남음
    detail = line_length_detail(line_length_of(review), files, lengths)
    assert "  코드 줄: 24 (91)" in detail
    assert "  주석·docstring: 23 (95)" in detail  # 23행은 docstring 줄


def test_only_line_length_leaves_style_section_empty():
    """E501만 있으면 스타일 이슈 섹션은 "(없음)", 지적 수 0건, 참고에만 나옴."""
    files = _files_03()
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[_style(24, ["ruff:E501"])],
    )
    text = render_review(review, files, title="t")
    style = text.split("## 스타일 이슈\n\n", 1)[1]
    assert style.startswith("(없음)")
    assert "줄 길이 초과 1줄 (기준 ?칸): app/chain.py 24" in text
    assert "지적 0건 (꼭 고칠 것 0 · 다듬을 것 0)" in text


# ── round4 이후: 고치는 방법 줄 비교 · 참고 지적 섹션 ───────────────────────


def test_fix_marks_only_changed_lines():
    """제안이 앞뒤 문맥 줄까지 다시 적으면 같은 줄은 표시 없이, 바뀐 줄만 -/+."""
    files = _files_03()
    lines = dp.new_side_lines(files[0])
    code = "\n".join(
        [lines[23].strip(), "model = model_id or DEFAULT_MODEL", lines[25].strip()]
    )
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[
            {
                "issue_type": "bug",
                "file": "app/chain.py",
                "line": 24,
                "severity": "high",
                "message": "m",
                "evidence": [{"type": "code", "source": "app/chain.py:24"}],
                "suggestion": f"```python\n{code}\n```",
            }
        ],
    )
    out = render_review(review, files, title="t").splitlines()
    fix = out[out.index("   고치는 방법:") + 1 :][:4]
    assert fix == [
        f"       {lines[23].rstrip()}",
        f"     - {lines[24].rstrip()}",
        "     +     model = model_id or DEFAULT_MODEL",
        f"       {lines[25].rstrip()}",
    ]


def _note(line, rule_id):
    return {
        "issue_type": "security",
        "file": "app/chain.py",
        "line": line,
        "severity": "low",
        "message": "참고: 인젝션 예시 문장입니다.",
        "evidence": [{"type": "tool", "source": "injection", "rule_id": rule_id}],
        "suggestion": "s",
    }


def test_notes_move_to_note_section_grouped_by_line():
    """low 참고 지적은 보안 번호 목록이 아니라 "## 참고"에, 같은 줄은 한 줄로.

    지적 수·HITL 번호에서 빠지고, Review JSON에는 그대로 남는다.
    """
    from src.report.render import ordered_items

    files = _files_03()
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[
            _note(5, "injection-scanner:ignore-instructions"),
            _note(5, "injection-scanner:claim-no-issues"),
            _style(17, ["ruff:E711"]),
        ],
    )
    text = render_review(review, files, title="t")
    security = text.split("### 보안\n\n", 1)[1].split("### 버그", 1)[0]
    assert security.strip() == "(없음)"
    note = text.split("## 참고\n", 1)[1].splitlines()
    assert note[0] == (
        "app/chain.py:5 · 인젝션 예시 문장 (ignore-instructions, claim-no-issues)"
        " — 리뷰 도구를 향한 지시로 판정하지 않음"
    )
    assert "지적 1건 (꼭 고칠 것 0 · 다듬을 것 1)" in text
    assert [len(it) for it in ordered_items(review)] == [1]
    assert len(review.findings) == 3


# ── 고치는 방법 적용 위치 (542b0bb 자기 리뷰) ─────────────────────────────


RUN_CASE = [  # 542b0bb evaluation/run_eval.py 86–97행 구조 (교육용 더미 코드)
    "def run_case(case, retries=3):",
    "    for attempt in range(retries):",
    "        out = run_query(case)",
    "        if not is_exec_error(out):",
    "            return out",
    "        time.sleep(20 * (attempt + 1))",
    "    return out",
]


def _run_case_review(suggestion: str):
    body = "".join(f"+{line}\n" for line in RUN_CASE)
    files = dp.parse_diff(
        "diff --git a/app/run.py b/app/run.py\n--- /dev/null\n+++ b/app/run.py\n"
        f"@@ -0,0 +1,{len(RUN_CASE)} @@\n{body}"
    )
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[
            {
                "issue_type": "bug",
                "file": "app/run.py",
                "line": 7,
                "severity": "medium",
                "message": "retries가 0이면 out이 정의되지 않습니다.",
                "evidence": [{"type": "code", "source": "app/run.py:7"}],
                "suggestion": suggestion,
            }
        ],
    )
    out = render_review(review, files, title="t").splitlines()
    return out[out.index("   고치는 방법:") + 1 :][:3]


def test_fix_is_placed_where_suggestion_lines_match():
    """지적은 return 줄이지만 수정안이 for 줄 앞 삽입이면 for 줄 기준으로 표시.

    return 줄을 지우고 for 줄을 더하는 틀린 diff가 나오면 안 된다.
    """
    fix = _run_case_review("```python\nout = None\nfor attempt in range(retries):\n```")
    assert fix == [
        "     +     out = None",
        "           for attempt in range(retries):",
        "",
    ]


def test_fix_without_matching_place_is_shown_as_block_with_location():
    """위치를 맞출 수 없고 지적한 줄과도 다르면 -/+ 없이 코드 블록과 적용 위치 안내."""
    fix = _run_case_review("```python\nif retries < 1:\n    raise ValueError\n```")
    assert fix == [
        "     적용 위치: 7행 앞/뒤 (자동으로 맞추지 못함 — 직접 확인)",
        "     | if retries < 1:",
        "     |     raise ValueError",
    ]


def test_fix_is_not_anchored_on_punctuation_only_line():
    """`),`처럼 기호만 있는 줄 하나가 맞는다고 그 위치에 넣지 않는다.

    542b0bb 재리뷰: 519행을 여러 줄로 바꾸는 제안이 518행 `),`에 맞춰져, 위 패턴의
    괄호 앞에 새 줄을 넣는 틀린 diff가 나왔다 → 지적한 줄을 고친 제안으로 표시.
    """
    code = [
        "PATTERNS = {",
        '    "a": re.compile(',
        '        r"x$"',
        "    ),",
        '    "b": re.compile(r"y$"),',
        "}",
    ]
    body = "".join(f"+{line}\n" for line in code)
    files = dp.parse_diff(
        "diff --git a/app/p.py b/app/p.py\n--- /dev/null\n+++ b/app/p.py\n"
        f"@@ -0,0 +1,{len(code)} @@\n{body}"
    )
    review = Review(
        summary=[f"요약 {i}" for i in range(1, 6)],
        findings=[
            {
                "issue_type": "bug",
                "file": "app/p.py",
                "line": 5,
                "severity": "medium",
                "message": "m",
                "evidence": [{"type": "code", "source": "app/p.py:5"}],
                "suggestion": '```python\n"b": re.compile(\n    r"y$",\n'
                "    re.MULTILINE,\n),\n```",
            }
        ],
    )
    out = render_review(review, files, title="t").splitlines()
    fix = out[out.index("   고치는 방법:") + 1 :][:5]
    assert fix == [
        '     -     "b": re.compile(r"y$"),',
        '     +     "b": re.compile(',
        '     +         r"y$",',
        "     +         re.MULTILINE,",
        "     +     ),",
    ]
