"""ruff가 자동으로 고칠 수 있는 스타일 지적의 고치는 방법을 ruff 결과로 바꿉니다.

리뷰어(LLM)가 쓴 고치는 방법은 import 순서를 틀리게 적는 등 ruff와 어긋날 수
있습니다(round4 이후 f8e0c93 재리뷰). 자동 수정이 가능한 규칙(`FIXABLE`)의 지적은
`ruff check --fix --diff` 결과를 고치는 방법으로 쓰고, 설명 문장이 그 규칙과
맞지 않으면(필수 낱말이 없거나 다른 ruff 코드를 말함) 고정 설명 문장으로
바꿉니다. 계산은 모두 코드로 하며 LLM을 부르지 않습니다.
"""

from __future__ import annotations

import re

from src.diff import parser as dp
from src.findings.schema import Finding
from src.toolkit.ruff import RUFF_TO_PEP8, ruff_fix_blocks

# 자동 수정 가능 규칙 → (고정 설명 문장, 설명 문장에 있어야 할 낱말 묶음들)
# 낱말 묶음마다 하나 이상이 설명에 있어야 "ruff 결과와 맞는 설명"으로 봅니다.
FIXABLE: dict[str, tuple[str, tuple[tuple[str, ...], ...]]] = {
    "I001": (
        "import 문이 정렬 규칙(표준 라이브러리 → 서드파티 → 로컬 그룹, 그룹 안은 "
        "알파벳순)에 맞지 않습니다. 순서가 일정해야 변경을 읽기 쉽고 병합 충돌이 "
        "줄어듭니다.",
        (("import",), ("정렬", "순서", "그룹", "sort")),
    ),
    "E401": (
        "한 줄에 여러 모듈을 import하고 있습니다. PEP 8은 import를 한 줄에 하나씩 "
        "쓰도록 권장합니다.",
        (("import",),),
    ),
    "E711": (
        "None과의 비교에 == 또는 !=를 쓰고 있습니다. None은 하나뿐인 객체이므로 "
        "is / is not으로 비교해야 합니다.",
        (("None",),),
    ),
    "E712": (
        "True·False와 ==로 비교하고 있습니다. 진릿값은 조건식에 그대로 쓰는 것이 "
        "PEP 8 권장입니다.",
        (("True", "False"),),
    ),
    "F401": (
        "import했지만 쓰지 않는 이름이 있습니다. 쓰지 않는 import는 읽는 사람을 "
        "헷갈리게 하고 불필요한 의존을 만듭니다.",
        (("import",), ("사용", "쓰지 않", "unused", "미사용")),
    ),
    "F541": (
        "자리 표시자가 없는 f-string입니다. 넣을 값이 없으면 일반 문자열로 충분합니다.",
        (("f-string", "f 문자열", "f문자열", "자리 표시자", "placeholder"),),
    ),
    "W291": (
        "줄 끝에 공백이 있습니다. 보이지 않는 공백은 불필요한 diff를 만듭니다.",
        (("공백", "whitespace"),),
    ),
    "W293": (
        "빈 줄에 공백이 있습니다. 보이지 않는 공백은 불필요한 diff를 만듭니다.",
        (("공백", "whitespace"),),
    ),
}

_RUFF_CODE_RE = re.compile(r"\b([EWFIN]\d{3})\b")

SUGGESTION_HEAD = "ruff 자동 수정(`ruff check --fix`) 결과입니다."


def _fixable_code(finding: Finding, truth: set[tuple[str, int, str]]) -> str | None:
    """지적에 대응하는 자동 수정 가능 ruff 코드 (그 줄에 실제 ruff 위반이 있을 때만).

    근거가 `ruff:<코드>`이면 그 코드, PEP 8 문서 근거면 연결표(RUFF_TO_PEP8)로
    같은 섹션에 대응하는 코드를 찾습니다.
    """
    if finding.issue_type != "style":
        return None
    rule_ids = {e.rule_id or "" for e in finding.evidence}
    for code in FIXABLE:
        if (finding.file, finding.line, code) not in truth:
            continue
        if f"ruff:{code}" in rule_ids or RUFF_TO_PEP8.get(code) in rule_ids:
            return code
    return None


def message_matches(code: str, message: str) -> bool:
    """설명 문장이 ruff 규칙과 맞는지: 필수 낱말이 있고 다른 ruff 코드를 말하지 않음."""
    _, groups = FIXABLE[code]
    if not all(any(w.lower() in message.lower() for w in g) for g in groups):
        return False
    return all(c == code for c in _RUFF_CODE_RE.findall(message))


def _with_context(
    lines: dict[int, str], block: tuple[int, int, list[str]]
) -> list[str]:
    """고친 줄 앞뒤에 바뀌지 않은 문맥 한 줄씩 붙입니다.

    줄을 지우기만 하는 수정(F401 등)도 화면에서 `-` 줄로 보이게 하기 위함입니다.
    """
    start, end, new = block
    before = [lines[start - 1]] if start - 1 in lines else []
    after = [lines[end + 1]] if end + 1 in lines else []
    return before + new + after


def apply_ruff_fixes(
    findings: list[Finding],
    py_files: list[dp.FileDiff],
    ruff_all: list[dict],
    settings=None,
) -> tuple[list[Finding], list[dict]]:
    """자동 수정 가능 규칙의 style 지적에 ruff 수정 결과를 고치는 방법으로 넣습니다.

    돌려주는 값: (바꾼 지적 목록, trace 기록) — 기록은 파일·줄·코드·설명 교체 여부.
    ruff가 그 줄을 고치지 못하면(파일 일부만 보여 F·I 규칙을 못 쓰는 경우 등)
    지적을 그대로 둡니다.
    """
    truth = {(r["file"], r["line"], r["code"]) for r in ruff_all}
    by_path = {f.path: f for f in py_files}
    cache: dict[tuple[str, str], list] = {}
    out, log = [], []
    for f in findings:
        code = _fixable_code(f, truth)
        file = by_path.get(f.file)
        if code is None or file is None:
            out.append(f)
            continue
        key = (f.file, code)
        if key not in cache:
            cache[key] = ruff_fix_blocks(file, {code}, settings)
        block = next((b for b in cache[key] if b[0] <= f.line <= b[1]), None)
        if block is None:
            out.append(f)
            continue
        fixed = _with_context(dp.new_side_lines(file), block)
        suggestion = SUGGESTION_HEAD + "\n```python\n" + "\n".join(fixed) + "\n```"
        matches = message_matches(code, f.message)
        update = {"suggestion": suggestion}
        if not matches:
            update["message"] = FIXABLE[code][0]
        out.append(f.model_copy(update=update))
        log.append(
            {
                "file": f.file,
                "line": f.line,
                "code": code,
                "message_replaced": not matches,
            }
        )
    return out, log
