"""ruff 검사: diff에서 변경 후 파일을 다시 만들어 변경된 줄의 위반만 돌려줍니다.

ruff 코드 → PEP 8 섹션 연결표(RUFF_TO_PEP8)도 여기에 둡니다.
"""

from __future__ import annotations

import json
import subprocess
import sys

from src.diff import parser as dp
from src.shared import review_settings

# ruff 코드 → PEP 8 근거 (evaluation/README.md 연결표와 같은 내용).
# ruff 위반을 지적할 때 search_evidence로 함께 찾아볼 문서 rule_id 힌트입니다.
RUFF_TO_PEP8 = {
    "E501": "PEP8-maximum-line-length",
    "E401": "PEP8-imports",
    "E402": "PEP8-imports",
    "I001": "PEP8-imports",
    "E711": "PEP8-programming-recommendations",
    "E712": "PEP8-programming-recommendations",
    "E721": "PEP8-programming-recommendations",
    "E722": "PEP8-programming-recommendations",
    "E731": "PEP8-programming-recommendations",
    "W291": "PEP8-whitespace-in-expressions-and-statements",
    "W293": "PEP8-whitespace-in-expressions-and-statements",
}


def _pep8_hint(code: str) -> str | None:
    if code.startswith("N8"):
        return "PEP8-naming-conventions"
    return RUFF_TO_PEP8.get(code)


def reconstruct_file(file: dp.FileDiff) -> tuple[str, bool]:
    """diff에서 변경 후 파일 내용을 다시 만듭니다.

    diff에 보이지 않는 줄은 빈 줄로 채워 줄 번호를 맞춥니다.
    돌려주는 두 번째 값은 "파일 전체가 diff 안에 보이는가"입니다
    (1행부터 끝까지 빈틈없이 보이면 True).
    """
    visible = dp.new_side_lines(file)
    if not visible:
        return "", True
    last = max(visible)
    complete = all(n in visible for n in range(1, last + 1))
    lines = [visible.get(n, "") for n in range(1, last + 1)]
    return "\n".join(lines) + "\n", complete


def run_ruff(file: dp.FileDiff, settings=None) -> list[dict]:
    """파일 하나에 ruff를 돌려 **변경된 줄**의 위반만 돌려줍니다.

    파일 일부만 보이는 diff에서는 파일 전체를 봐야 하는 pyflakes(F)와
    import 정렬(I) 규칙이 가짜 위반을 내므로 제외합니다.
    `settings`(review_settings.ReviewSettings)가 저장소 ruff 설정을 가리키면
    그 설정으로,
    없으면 CiteReview `ruff.toml`로 검사합니다 (평가는 항상 기본값).
    """
    settings = settings or review_settings.DEFAULT_SETTINGS
    text, complete = reconstruct_file(file)
    if not text:
        return []
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--config",
        str(settings.ruff_config),
        "--output-format",
        "json",
        "--stdin-filename",
        file.path,
        "-",
    ]
    if not complete:
        cmd[4:4] = ["--extend-ignore", "F,I"]
    result = subprocess.run(
        cmd,
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=settings.ruff_cwd,
    )
    changed = dp.changed_lines(file)
    findings = []
    for v in json.loads(result.stdout or "[]"):
        line = v["location"]["row"]
        if line not in changed:
            continue
        code = v["code"]
        findings.append(
            {
                "file": file.path,
                "line": line,
                "code": code,
                "rule_id": f"ruff:{code}",
                "message": v["message"],
                "pep8_rule_id": _pep8_hint(code),
            }
        )
    return findings
