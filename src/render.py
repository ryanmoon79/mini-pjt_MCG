"""렌더러: `Review` → 터미널 Markdown (형식 기준 docs/output_example.md).

- **LLM을 호출하지 않습니다.** `Review`와 `diff_parser` 결과만으로 문자열을 만듭니다.
- 문제 코드 발췌는 `diff_parser.excerpt()`를 쓰고, 여기서 diff를 다시 파싱하지 않습니다
  (호출하는 쪽이 이미 파싱·마스킹한 `FileDiff` 목록을 넘김).
- 섹션: 요약 / 잠재 버그(보안·버그 소제목) / 스타일 이슈 / 추천 개선안.
  섹션 안은 심각도 high > medium > low 순, 지적 번호는 문서 전체에서 이어 붙입니다.
- 내부 로그는 `render_verbose()`로 따로 만들며 `--verbose`에서만 붙입니다.
"""

from __future__ import annotations

import json
import re

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
_CODE_BLOCK_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)


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
    out = [f"   고치는 방법: {rest}".rstrip(), f"     - {current.strip()}"]
    code = match.group(1).rstrip("\n").split("\n")
    out += [f"     + {ln}" for ln in code[:MAX_FIX_LINES]]
    if len(code) > MAX_FIX_LINES:  # 긴 제안은 앞부분만, 전체는 --verbose에서
        out.append("       …(전체는 --verbose)")
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
) -> str:
    """리뷰 결과를 docs/output_example.md 형식의 문자열로 만듭니다."""
    files = {f.path: f for f in py_files}
    titles = doc_titles or {}
    must = [f for f in review.findings if f.severity in MUST_FIX]
    counts = [
        f"{TYPE_LABEL[t]} {n}"
        for t in ("security", "bug", "style")
        if (n := sum(1 for f in must if f.issue_type == t))
    ]
    skipped_text = ", ".join(f"{p} (.py 아님)" for p in (skipped or [])) or "없음"
    out = [
        f"CiteReview · {title}",
        f"리뷰: {', '.join(files) or '없음'} · 건너뜀: {skipped_text} · "
        f"마스킹: {_masked_summary(masked or {})}",
        f"꼭 고쳐야 할 것 {len(must)}건 ({', '.join(counts)})"
        if must
        else "꼭 고쳐야 할 것 없음",
        *([NO_ISSUES] if not review.findings else []),
        "",
        "## 요약",
        *[f"{i}. {s}" for i, s in enumerate(review.summary, 1)],
        "",
        "## 잠재 버그",
        "",
    ]
    no = 0
    for issue_type in ("security", "bug"):
        group = _sorted([f for f in review.findings if f.issue_type == issue_type])
        out += [f"### {TYPE_LABEL[issue_type]}", ""]
        if not group:
            out += ["(없음)", ""]
        for f in group:
            no += 1
            out += _render_finding(no, f, files, titles)
    out += ["## 스타일 이슈", ""]
    style = _sorted([f for f in review.findings if f.issue_type == "style"])
    if not style:
        out += ["(없음)", ""]
    for f in style:
        no += 1
        out += _render_finding(no, f, files, titles)
    out += ["## 추천 개선안", *[f"- {r}" for r in review.recommendations], ""]
    polish = len(review.findings) - len(must)
    out.append(
        f"지적 {len(review.findings)}건 "
        f"(꼭 고칠 것 {len(must)} · 다듬을 것 {polish}) · "
        f"근거가 확인되지 않아 제외된 지적 {dropped}건 (--verbose로 확인)"
    )
    return "\n".join(out)


def render_rejection(title: str, reason: str) -> str:
    """거절 안내 (.py 없음, 300줄 초과)."""
    return f"CiteReview · {title}\n리뷰를 거절했습니다: {reason}"


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
    """화면에 보이는 번호 순서(보안 → 버그 → 스타일, 각 심각도순)의 지적 목록.

    HITL의 "항목별 무시"가 화면 번호로 지적을 고를 때 씁니다.
    """
    out: list[Finding] = []
    for issue_type in ("security", "bug", "style"):
        out += _sorted([f for f in review.findings if f.issue_type == issue_type])
    return out
