"""렌더러: `Review` → 터미널 Markdown (형식 기준 docs/output_example.md).

- **LLM을 호출하지 않습니다.** `Review`와 `diff_parser` 결과만으로 문자열을 만듭니다.
- 문제 코드 발췌는 `diff_parser.excerpt()`를 쓰고, 여기서 diff를 다시 파싱하지 않습니다
  (호출하는 쪽이 이미 파싱·마스킹한 `FileDiff` 목록을 넘김).
- 섹션: 요약 / 잠재 버그(보안·버그 소제목) / 스타일 이슈 / 추천 개선안.
  섹션 안은 심각도 high > medium > low 순, 지적 번호는 문서 전체에서 이어 붙입니다.
- 내부 로그는 `render_verbose()`로 따로 만들며 `--verbose`에서만 붙입니다.
- 화면 표시 규칙 (Review JSON·평가는 그대로, 표시만 바꿈):
  - 한 파일의 줄 길이(E501) 지적은 **한 항목으로 묶고** 수정안을 붙이지 않습니다.
    줄 번호는 "코드 줄"과 "주석·docstring"으로 나누고, 길이는 ruff가 잰 값을 씁니다.
  - 고치는 방법의 `+` 줄에는 `-` 줄(원래 줄)의 들여쓰기를 붙입니다
    (`+` 줄끼리의 상대 들여쓰기는 유지).
  - 화면 번호는 묶음 단위이며, HITL "항목별 무시"도 같은 번호(`ordered_items`)를 씁니다.
"""

from __future__ import annotations

import io
import json
import re
import textwrap
import tokenize

from src import diff_parser as dp
from src.schema import Evidence, Finding, Review

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
MUST_FIX = {"high", "medium"}
TYPE_LABEL = {"security": "보안", "bug": "버그", "style": "스타일"}
TOOL_TITLES = {
    "secret-scanner": "비밀값 탐지",
    "pii-scanner": "개인정보 탐지",
    "injection-scanner": "프롬프트 인젝션 탐지",
}
SECRET_PATTERN_NAMES = {"aws-access-key", "aws-secret-key", "api-key", "password"}
MAX_FIX_LINES = 5  # 고치는 방법의 + 줄 최대 표시 수 (넘으면 --verbose에서 전체)
NO_ISSUES = "문제를 찾지 못했습니다."
# 결과를 받지 못한 리뷰어 표시용 (src/reviewers.py의 BUG·STYLE 이름)
REVIEWER_LABELS = {"bug_reviewer": "버그 리뷰어", "style_reviewer": "스타일 리뷰어"}
_CODE_BLOCK_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)
E501_IDS = {"ruff:E501", "PEP8-maximum-line-length"}
LINE_LENGTH_HINT = (
    "나누는 방법: 괄호 안에서 줄을 바꾸거나, 긴 문자열·주석·docstring을 여러 줄로 "
    "나눕니다(줄마다 수정안은 붙이지 않음). 기준이 팀과 다르면 저장소 자체 ruff 설정"
    "(line-length)으로 조정하세요."
)


def _sorted(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.file, f.line))


def evidence_label(ev: Evidence, titles: dict[str, str]) -> str:
    """근거 표시: 문서 제목이나 도구 이름을 먼저, 규칙 ID는 괄호로 뒤에."""
    rid = ev.rule_id or ""
    if ev.type == "doc":
        return f"{titles.get(rid, rid)} ({rid})"
    if rid.startswith("ruff:"):
        return f"ruff {rid.removeprefix('ruff:')} ({rid})"
    if ev.type == "tool":
        scanner = rid.split(":")[0] or ev.source
        return f"{TOOL_TITLES.get(scanner, scanner)} ({rid})"
    return f"코드 위치 ({ev.source})"


def _excerpt_lines(file: dp.FileDiff | None, line: int) -> list[str]:
    if file is None:
        return []
    rows = dp.excerpt(file, line, radius=1)
    width = max((len(str(n)) for n, _ in rows), default=1)
    return [
        f"    {'>' if n == line else ' '} {n:>{width}} | {text}" for n, text in rows
    ]


def _fix_lines(finding: Finding, file: dp.FileDiff | None) -> list[str]:
    """고치는 방법: ```python 블록이 있으면 문제 줄을 -, 블록 내용을 +로."""
    match = _CODE_BLOCK_RE.search(finding.suggestion)
    if not match:
        return [f"   고치는 방법: {finding.suggestion.strip()}"]
    current = dp.new_side_lines(file).get(finding.line, "") if file else ""
    # 설명 문장이 있으면 "고치는 방법:" 뒤에 먼저 쓰고, 그 아래 -/+ 줄을 둠
    rest = " ".join(_CODE_BLOCK_RE.sub("", finding.suggestion).split())
    # - 줄은 원래 들여쓰기 그대로,
    # + 줄은 공통 들여쓰기를 걷어 낸 뒤 - 줄의 들여쓰기를 붙임
    indent = current[: len(current) - len(current.lstrip())]
    out = [f"   고치는 방법: {rest}".rstrip(), f"     - {current.rstrip()}"]
    code = textwrap.dedent(match.group(1).rstrip("\n")).split("\n")
    out += [
        f"     + {indent}{ln}".rstrip() if ln.strip() else "     +"
        for ln in code[:MAX_FIX_LINES]
    ]
    if len(code) > MAX_FIX_LINES:  # 긴 제안은 앞부분만, 전체는 --verbose에서
        out.append("       …(전체는 --verbose)")
    return out


def is_line_length(f: Finding) -> bool:
    """줄 길이(E501) 스타일 지적인지: ruff E501 또는 PEP 8 최대 줄 길이 근거."""
    return f.issue_type == "style" and any(e.rule_id in E501_IDS for e in f.evidence)


def _item_severity(item: list[Finding]) -> str:
    return min((f.severity for f in item), key=SEVERITY_ORDER.__getitem__)


def _items_of(findings: list[Finding], issue_type: str) -> list[list[Finding]]:
    """한 유형의 표시 항목: style은 파일별 E501을 한 항목으로 묶음."""
    group = _sorted([f for f in findings if f.issue_type == issue_type])
    if issue_type != "style":
        return [[f] for f in group]
    items: list[list[Finding]] = []
    by_file: dict[str, list[Finding]] = {}
    for f in group:
        if is_line_length(f):
            by_file.setdefault(f.file, []).append(f)
        else:
            items.append([f])
    items += [sorted(fs, key=lambda f: f.line) for fs in by_file.values()]
    return sorted(
        items,
        key=lambda it: (SEVERITY_ORDER[_item_severity(it)], it[0].file, it[0].line),
    )


def ordered_items(review: Review) -> list[list[Finding]]:
    """화면 번호 순서(보안 → 버그 → 스타일, 각 심각도순)의 표시 항목 목록.

    E501 묶음은 한 번호에 여러 지적이 들어 있습니다. HITL의 "항목별 무시"가
    화면 번호로 지적을 고를 때 씁니다 (묶음 번호를 빼면 그 파일의 E501 전부 제외).
    """
    out: list[list[Finding]] = []
    for issue_type in ("security", "bug", "style"):
        out += _items_of(review.findings, issue_type)
    return out


def _line_kinds(file: dp.FileDiff | None) -> dict[int, str]:
    """변경 후 줄마다 "code" 또는 "comment"(주석·docstring만 있는 줄).

    diff에 보이는 줄로 파일을 다시 만들어 tokenize로 판정하고, 실패하면(일부만 보이는
    diff 등) `#`로 시작하는 줄만 주석으로 봅니다.
    """
    if file is None:
        return {}
    lines = dp.new_side_lines(file)
    if not lines:
        return {}
    text = "\n".join(lines.get(n, "") for n in range(1, max(lines) + 1)) + "\n"
    kinds = {n: "comment" for n in lines}
    code_rows: set[int] = set()
    skip = {
        tokenize.COMMENT,
        tokenize.STRING,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in skip:
                code_rows.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return {
            n: "comment" if t.lstrip().startswith("#") else "code"
            for n, t in lines.items()
        }
    for n in code_rows:
        if n in kinds:
            kinds[n] = "code"
    return kinds


def _render_line_length(
    no: int,
    item: list[Finding],
    files: dict[str, dp.FileDiff],
    titles: dict[str, str],
    lengths: dict[tuple[str, int], tuple[int, int]],
) -> list[str]:
    """파일 하나의 E501 묶음: 줄 번호를 코드 줄·주석·docstring으로 나눠 한 항목으로."""
    path = item[0].file
    kinds = _line_kinds(files.get(path))
    rows = sorted({f.line for f in item})
    limit = next((lim for (p, _), (_, lim) in lengths.items() if p == path), None)

    def fmt(n: int) -> str:
        size = lengths.get((path, n))
        return f"{n} ({size[0]})" if size else str(n)

    code = [fmt(n) for n in rows if kinds.get(n, "code") == "code"]
    comment = [fmt(n) for n in rows if kinds.get(n) == "comment"]
    seen, evidence = set(), []
    for f in item:
        for e in f.evidence:
            if e.type != "code" and (e.rule_id or e.source) not in seen:
                seen.add(e.rule_id or e.source)
                evidence.append(evidence_label(e, titles))
    out = [
        f"{no}. {path} · 줄 길이 초과 {len(rows)}줄 · {_item_severity(item)}",
        f"   한 줄이 {limit or '기준'}칸(ruff 기준, 한글은 2칸)을 넘습니다. "
        f"코드 줄 {len(code)}, 주석·docstring {len(comment)}",
    ]
    if code:
        out.append("   코드 줄: " + ", ".join(code))
    if comment:
        out.append("   주석·docstring: " + ", ".join(comment))
    out += ["   근거: " + ", ".join(evidence), f"   {LINE_LENGTH_HINT}", ""]
    return out


def _render_finding(
    no: int, f: Finding, files: dict[str, dp.FileDiff], titles: dict[str, str]
) -> list[str]:
    file = files.get(f.file)
    lines = [f"{no}. {f.file}:{f.line} · {f.severity}", f"   {f.message.strip()}"]
    lines += _excerpt_lines(file, f.line)
    lines.append("   근거: " + ", ".join(evidence_label(e, titles) for e in f.evidence))
    lines += _fix_lines(f, file)
    return lines + [""]


def _masked_summary(masked: dict[str, int]) -> str:
    secret = sum(n for k, n in masked.items() if k in SECRET_PATTERN_NAMES)
    pii = sum(n for k, n in masked.items() if k not in SECRET_PATTERN_NAMES)
    parts = [f"비밀값 {secret}건"] * bool(secret) + [f"개인정보 {pii}건"] * bool(pii)
    return ", ".join(parts) or "없음"


def render_review(
    review: Review,
    py_files: list[dp.FileDiff],
    *,
    title: str,
    skipped: list[str] | None = None,
    masked: dict[str, int] | None = None,
    dropped: int = 0,
    doc_titles: dict[str, str] | None = None,
    line_lengths: dict[tuple[str, int], tuple[int, int]] | None = None,
    criteria: str | None = None,
    unchecked: list[str] | None = None,
) -> str:
    """리뷰 결과를 docs/output_example.md 형식의 문자열로 만듭니다.

    `line_lengths`: {(파일, 줄): (ruff가 잰 길이, 기준)} — E501 묶음에 길이를 표시.
    건수는 화면 항목 기준입니다 (E501 묶음 1개 = 1건).
    `criteria`: 적용된 기준(줄 길이·변경 한도) — 맨 위 안내 줄 끝에 붙입니다.
    `unchecked`: 결과를 받지 못한 리뷰어 이름(trace `structured`가 none).
    "문제 없음"과 구분되게 맨 위에 "결과 없음(확인 못 함)"을 표시하고, 그 리뷰어가
    맡은 빈 섹션도 "(없음)" 대신 "(확인 못 함)"으로 둡니다.
    """
    unchecked = [n for n in (unchecked or []) if n in REVIEWER_LABELS]
    files = {f.path: f for f in py_files}
    titles = doc_titles or {}
    lengths = line_lengths or {}
    items = ordered_items(review)
    must = [it for it in items if _item_severity(it) in MUST_FIX]
    counts = [
        f"{TYPE_LABEL[t]} {n}"
        for t in ("security", "bug", "style")
        if (n := sum(1 for it in must if it[0].issue_type == t))
    ]
    skipped_text = ", ".join(f"{p} (.py 아님)" for p in (skipped or [])) or "없음"
    out = [
        f"CiteReview · {title}",
        f"리뷰: {', '.join(files) or '없음'} · 건너뜀: {skipped_text} · "
        f"마스킹: {_masked_summary(masked or {})}"
        + (f" · 기준: {criteria}" if criteria else ""),
        *[f"주의: {REVIEWER_LABELS[n]} 결과 없음(확인 못 함)" for n in unchecked],
        f"꼭 고쳐야 할 것 {len(must)}건 ({', '.join(counts)})"
        if must
        else "꼭 고쳐야 할 것 없음",
        # 리뷰어 결과가 빠졌으면 "문제를 찾지 못했습니다"(확인했고 문제 없음)를 안 씀
        *([NO_ISSUES] if not review.findings and not unchecked else []),
        "",
        "## 요약",
        *[f"{i}. {s}" for i, s in enumerate(review.summary, 1)],
        "",
        "## 잠재 버그",
        "",
    ]
    no = 0

    def render_items(group: list[list[Finding]], owner: str) -> list[str]:
        nonlocal no
        empty = (
            f"(확인 못 함: {REVIEWER_LABELS[owner]} 결과 없음)"
            if owner in unchecked
            else "(없음)"
        )
        lines: list[str] = [] if group else [empty, ""]
        for it in group:
            no += 1
            if is_line_length(it[0]):
                lines += _render_line_length(no, it, files, titles, lengths)
            else:
                lines += _render_finding(no, it[0], files, titles)
        return lines

    for issue_type in ("security", "bug"):
        out += [f"### {TYPE_LABEL[issue_type]}", ""]
        out += render_items(_items_of(review.findings, issue_type), "bug_reviewer")
    out += ["## 스타일 이슈", ""]
    out += render_items(_items_of(review.findings, "style"), "style_reviewer")
    out += ["## 추천 개선안", *[f"- {r}" for r in review.recommendations], ""]
    polish = len(items) - len(must)
    out.append(
        f"지적 {len(items)}건 "
        f"(꼭 고칠 것 {len(must)} · 다듬을 것 {polish}) · "
        f"근거가 확인되지 않아 제외된 지적 {dropped}건 (--verbose로 확인)"
    )
    return "\n".join(out)


def render_rejection(title: str, reason: str) -> str:
    """거절 안내 (.py 없음, 300줄 초과)."""
    return f"CiteReview · {title}\n리뷰를 거절했습니다: {reason}"


def render_usage(usage: dict) -> list[str]:
    """trace `usage` 이벤트를 역할별 표 + 합계 줄로 (--verbose)."""
    total = usage.get("total", {})
    lines = [
        "## 토큰 (역할별, --verbose)",
        f"합계: 호출 {total.get('calls', 0)}회 · 입력 {total.get('input_tokens', 0):,}"
        f" · 출력 {total.get('output_tokens', 0):,}",
    ]
    for role, row in usage.get("by_role", {}).items():
        lines.append(
            f"- {role}: 호출 {row['calls']}회 · 입력 {row['input_tokens']:,}"
            f" · 출력 {row['output_tokens']:,}"
        )
    return lines


def render_verbose(trace: list[dict]) -> str:
    """`--verbose`용 내부 로그: 가드레일·planner·도구 호출·출력 검증 이벤트."""
    out = ["", "## 내부 로그 (--verbose)"]
    review = next((e["review"] for e in trace if e["type"] == "review"), None)
    for e in trace:
        if e["type"] in {"start", "end", "review"}:
            continue
        fields = {k: v for k, v in e.items() if k not in {"type", "t", "elapsed_ms"}}
        out.append(
            f"[{e['elapsed_ms']:>6} ms] {e['type']}: "
            f"{json.dumps(fields, ensure_ascii=False)}"
        )
    usage = next((e for e in trace if e["type"] == "usage"), None)
    if usage:
        out += ["", *render_usage(usage)]
    if review and review["findings"]:
        out += ["", "## 고치는 방법 전체 (--verbose)"]
        for f in review["findings"]:
            out += [
                f"- {f['file']}:{f['line']} ({f['issue_type']})",
                f["suggestion"],
                "",
            ]
    return "\n".join(out)


def ordered_findings(review: Review) -> list[Finding]:
    """화면 순서대로 펼친 지적 목록 (묶음 안의 지적까지 모두)."""
    return [f for item in ordered_items(review) for f in item]
