"""요약 5줄: 2·3·4줄은 코드가 만들고, LLM이 쓴 1·5줄은 결정적 규칙으로 검사합니다.

종합 노드에 넘길 지적 목록 정리와 추천 개선안의 줄 길이 항목 정리도 여기에 둡니다.
"""

from __future__ import annotations

import re

from src.diff import parser as dp
from src.findings.notes import is_note, is_unsure
from src.findings.schema import Finding
from src.guardrails.output import (
    mentions_classic,
)
from src.report.render import (
    TYPE_LABEL,
    is_line_length,
)

# 요약의 LLM 줄(change·action) 규칙 검사 (결정적)
SPECULATIVE_RE = re.compile(
    r"(것\s*같|보입니다|보임|추정|아마|듯합|듯이|일\s*수\s*있|수도\s*있|가능성"
    r"|양호|적절|훌륭|잘\s*(작성|되어|구현)|좋습니다|깔끔|우수|완벽"
    r"|문제\s*(가|는)?\s*없)"
)


# 줄 길이 숫자 검사 (줄 길이를 언급한 문장에만)
_SENTENCE_RE = re.compile(r"(?<=[.!?。])\s+|\n+")


_LENGTH_TOPIC_RE = re.compile(r"줄\s*길이|E501|line[-_ ]?length", re.IGNORECASE)


_LENGTH_NUM_RE = re.compile(
    r"(\d+)\s*(?:자|칸|글자|characters?|chars?|columns?)", re.IGNORECASE
)


_HANGUL_RE = re.compile(r"[가-힣]")  # 요약 LLM 줄은 한국어 (gpt-oss는 영어로 쓰기도 함)


_LINE_REF_RE = re.compile(r"(\d+)\s*(?:번\s*줄|번째\s*줄|행)")
_PY_FILE_RE = re.compile(r"[\w./-]*?([\w-]+\.py)\b")


def _file_line_refs(text: str) -> list[tuple[str, int]]:
    """문장에서 (파일 이름, 줄 번호) 쌍.

    파일 이름 뒤부터 다음 파일 이름 전까지의 줄 번호를 그 파일의 줄로 봅니다.
    예: "a.py 16번 줄, b.py 16번 줄" → [("a.py", 16), ("b.py", 16)]
    """
    marks = list(_PY_FILE_RE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out += [(m.group(1), int(n)) for n in _LINE_REF_RE.findall(text[m.end() : end])]
    return out


def _must(f: Finding) -> bool:
    """꼭 고칠 것: high·medium 중 참고·판단 필요가 아닌 지적."""
    return f.severity in {"high", "medium"} and not is_note(f) and not is_unsure(f)


def secbug_line(findings: list[Finding]) -> str:
    """요약 2줄 (코드): 보안·버그 결과.

    참고(low: 인젝션 예시·더미 표시 비밀값)는 세지 않습니다.
    """
    real = [f for f in findings if not is_note(f)]
    sec = [f for f in real if f.issue_type == "security"]
    bug = [f for f in real if f.issue_type == "bug"]
    if not sec and not bug:
        return "보안·버그 지적은 없습니다."
    must = [f for f in sec + bug if _must(f)]
    unsure = [f for f in sec + bug if is_unsure(f)]
    first = sorted(must or sec + bug, key=lambda f: (f.file, f.line))[0]
    checks = f", 확인 필요는 {len(unsure)}건" if unsure else ""
    return (
        f"보안 지적 {len(sec)}건, 버그 지적 {len(bug)}건이며 꼭 고칠 것은 "
        f"{len(must)}건{checks}입니다 (먼저 볼 곳: {first.file} {first.line}행)."
    )


def style_line(findings: list[Finding]) -> str:
    """요약 3줄 (코드): 스타일 결과를 한 줄로 (줄 길이 묶음 포함)."""
    style = [f for f in findings if f.issue_type == "style"]
    long_lines = [f for f in style if is_line_length(f)]
    others = [f for f in style if not is_line_length(f)]
    parts = []
    if long_lines:
        parts.append(f"줄 길이 초과 {len(long_lines)}줄")
    if others:
        codes = sorted(
            {
                (e.rule_id or "").removeprefix("ruff:")
                for f in others
                for e in f.evidence[:1]
                if e.rule_id
            }
        )
        label = f" ({', '.join(codes[:3])})" if codes else ""
        parts.append(f"그 밖의 스타일 {len(others)}건{label}")
    return "스타일: " + ", ".join(parts) + "." if parts else "스타일 지적은 없습니다."


def scope_line(files: list[str], skipped: list[str], settings) -> str:
    """요약 4줄 (코드): 리뷰 범위·건너뛴 파일·적용된 기준."""
    return (
        f"리뷰 범위: {', '.join(files) or '없음'}; "
        f"건너뜀: {', '.join(skipped) or '없음'}; 기준: {settings.label()}."
    )


def length_number_violation(text: str, lengths: set[int] | None) -> str | None:
    """줄 길이를 언급한 문장의 숫자가 실제 값(적용 기준·ruff가 잰 길이)인지 검사.

    줄 길이·E501·line-length를 언급한 문장에만 적용합니다. 그 문장 안의
    "숫자 + 자·칸·글자·characters" 값이 `lengths`에 없으면 위반입니다
    (예: 기준 88칸인데 "79자 이하로 줄이세요"). `lengths`가 None이면 검사하지 않음.
    """
    if not lengths:
        return None
    for sentence in _SENTENCE_RE.split(text):
        if not _LENGTH_TOPIC_RE.search(sentence):
            continue
        wrong = [n for n in _LENGTH_NUM_RE.findall(sentence) if int(n) not in lengths]
        if wrong:
            return f"줄 길이 숫자 불일치 ({', '.join(wrong)})"
    return None


def change_violation(text: str, lengths: set[int] | None = None) -> str | None:
    """요약 1줄(LLM, 변경 내용)의 규칙 위반 사유. 없으면 None."""
    if not text.strip():
        return "빈 줄"
    if not _HANGUL_RE.search(text):
        return "한국어 아님"
    if mentions_classic(text):
        return "langchain_classic 권장"
    if SPECULATIVE_RE.search(text):
        return "추측·평가성 표현"
    if len(text) > 240:
        return "너무 긺"
    return length_number_violation(text, lengths)


def action_violation(
    text: str, findings: list[Finding], lengths: set[int] | None = None
) -> str | None:
    """요약 5줄(LLM, 할 일)의 규칙 위반 사유. 지적 목록 밖의 줄 번호도 위반."""
    if not text.strip():
        return "빈 줄"
    if not _HANGUL_RE.search(text):
        return "한국어 아님"
    if mentions_classic(text):
        return "langchain_classic 권장"
    if SPECULATIVE_RE.search(text) and not text.strip().startswith("꼭 고쳐야 할 것은"):
        return "평가성 표현"
    lines = {f.line for f in findings}
    refs = {int(n) for n in _LINE_REF_RE.findall(text)}
    if any(n not in lines for n in refs):
        return "지적 목록에 없는 줄 번호"
    # 파일과 줄을 함께 말하면 그 (파일, 줄)이 지적 목록에 있어야 함
    # (cd65c0f: 지적은 guard_v1.py 16행뿐인데 "submission.py 16번 줄"도 말함)
    have = {(f.file.rsplit("/", 1)[-1], f.line) for f in findings}
    if any(pair not in have for pair in _file_line_refs(text)):
        return "지적 목록에 없는 파일·줄"
    # 판단 필요 지적만 있는 줄을 말하면 "확인"으로만 (단정형 "~하므로 고치세요" 금지)
    sure = {f.line for f in findings if not is_unsure(f)}
    unsure = {f.line for f in findings if is_unsure(f)} - sure
    if refs & unsure and "확인" not in text:
        return "판단 필요 지적을 단정"
    return length_number_violation(text, lengths)


def change_fallback(py_files: list[dp.FileDiff]) -> str:
    """요약 1줄 대체 문장 (코드): 파일별 추가·삭제 줄 수."""
    parts = [f"{f.path}(+{f.added_count}/-{f.deleted_count})" for f in py_files]
    return f"변경: {', '.join(parts) or '없음'} (추가/삭제 줄 수)."


def action_fallback(findings: list[Finding]) -> str:
    """요약 5줄 대체 문장 (코드): 가장 심각한 꼭 고칠 것 하나."""
    order = {"high": 0, "medium": 1}
    must = sorted(
        (f for f in findings if _must(f)),
        key=lambda f: (order[f.severity], f.file, f.line),
    )
    if not must:
        return "꼭 고쳐야 할 것은 없습니다."
    f = must[0]
    return f"먼저 {f.file} {f.line}행의 {TYPE_LABEL[f.issue_type]} 지적을 고치세요."


_LENGTH_REC_RE = re.compile(
    r"줄\s*길이|긴\s*줄|E501|line[-_ ]?length|\d+\s*(?:자|칸)\s*(?:이하|이내|를 넘)",
    re.IGNORECASE,
)


def limit_line_length_recs(recs: list[str], findings: list[Finding]) -> list[str]:
    """추천 개선안의 줄 길이 항목 정리 (결정적).

    다른 지적(줄 길이·참고 제외)이 있으면 줄 길이 항목은 넣지 않고,
    줄 길이 지적만 있으면 최대 1개만 남깁니다 (줄 길이는 화면 끝 "## 참고"에 있음).
    """
    others = [f for f in findings if not is_line_length(f) and not is_note(f)]
    out, kept_length = [], False
    for r in recs:
        if _LENGTH_REC_RE.search(r):
            if others or kept_length:
                continue
            kept_length = True
        out.append(r)
    return out


def synth_payload(findings: list[Finding]) -> list[dict]:
    """종합 노드에 넘길 지적 목록 (요약 규칙용으로 정리, Review JSON은 그대로).

    - security·bug를 앞에, style을 뒤에 둡니다.
    - "참고"(low: 인젝션 예시 문장·더미 표시 비밀값)는 요약에서 빼므로 넘기지 않습니다.
    - "판단 필요" 지적에는 `판단_필요: true`를 붙여 요약이 단정하지 않게 합니다.
    - 파일별 줄 길이(E501) 지적은 "줄 길이 초과 N줄" 한 건으로 묶어 넘깁니다.
    """
    order = {"security": 0, "bug": 1, "style": 2}
    out: list[dict] = []
    lengths: dict[str, list[int]] = {}
    for f in sorted(findings, key=lambda f: (order[f.issue_type], f.file, f.line)):
        if is_note(f):
            continue
        if is_line_length(f):
            lengths.setdefault(f.file, []).append(f.line)
            continue
        row = f.model_dump(
            include={"issue_type", "file", "line", "severity", "message", "evidence"}
        )
        if is_unsure(f):  # 요약에서 단정하지 말고 "확인"으로만 쓸 지적
            row["판단_필요"] = True
        out.append(row)
    for path, rows in lengths.items():
        out.append(
            {
                "issue_type": "style",
                "file": path,
                "line": min(rows),
                "severity": "low",
                "message": (
                    f"줄 길이 초과: 긴 줄 {len(rows)}개 "
                    f"(줄 번호 {', '.join(map(str, rows))})"
                ),
                "evidence": [{"type": "tool", "rule_id": "ruff:E501"}],
            }
        )
    return out
