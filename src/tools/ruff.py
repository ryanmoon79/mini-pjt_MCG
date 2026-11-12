"""ruff 검사: diff에서 변경 후 파일을 다시 만들어 변경된 줄의 위반만 돌려줍니다.

ruff 코드 → PEP 8 섹션 연결표(RUFF_TO_PEP8)도 여기에 둡니다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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


_NEUTRAL_DIR: Path | None = None


def _neutral_dir() -> Path:
    """CiteReview `ruff.toml` 사본을 둔 빈 임시 폴더 (프로세스당 하나).

    ruff는 실행 폴더·설정 파일 폴더를 프로젝트로 보고 그 안의 모듈로 import의
    first-party 여부를 정합니다. CiteReview 폴더에서 돌리면 리뷰 대상과 관계없는
    CiteReview 자신의 `src/` 구조가 I001 판정을 바꾸므로(round4 이후 f8e0c93 재리뷰의
    가짜 I001), 저장소 폴더가 없을 때는 아무 모듈도 없는 이 폴더에서 돌립니다.
    """
    global _NEUTRAL_DIR
    if _NEUTRAL_DIR is None or not _NEUTRAL_DIR.is_dir():
        _NEUTRAL_DIR = Path(tempfile.mkdtemp(prefix="citereview_ruff_"))
        shutil.copyfile(review_settings.RUFF_CONFIG_PATH, _NEUTRAL_DIR / "ruff.toml")
    return _NEUTRAL_DIR


def _ruff_base(settings, path: str) -> tuple[list[str], Path]:
    """ruff 명령의 설정 인자와 실행 폴더.

    - 저장소 폴더가 있으면(`--repo-dir`, GUI 커밋 모드): 그 저장소 설정·폴더 그대로.
    - 없으면(diff 파일만, 평가 포함): 중립 임시 폴더의 `ruff.toml` 사본으로 돌리고,
      diff 파일 경로의 최상위 폴더(예: `app/x.py` → `app`)를 known-first-party로
      지정합니다. 같은 diff 안의 다른 최상위 폴더는 서드파티로 봅니다.
    """
    if settings.ruff_cwd != review_settings.ROOT_DIR:
        return ["--config", str(settings.ruff_config)], settings.ruff_cwd
    base = _neutral_dir()
    args = ["--config", str(base / "ruff.toml")]
    top = path.split("/", 1)[0] if "/" in path else ""
    if top.isidentifier():
        args += ["--config", f"lint.isort.known-first-party = ['{top}']"]
    return args, base


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
    config_args, cwd = _ruff_base(settings, file.path)
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        *config_args,
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
        cwd=cwd,
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


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def ruff_fix_blocks(
    file: dp.FileDiff, codes: set[str], settings=None
) -> list[tuple[int, int, list[str]]]:
    """ruff 자동 수정(`--fix --unsafe-fixes --diff`) 결과를 덩어리 단위로 돌려줍니다.

    돌려주는 값: (원래 시작 줄, 원래 끝 줄, 고친 줄 목록) — 줄 번호는 변경 후 파일
    기준이고, ruff diff의 hunk 하나가 덩어리 하나(첫 변경 줄부터 마지막 변경 줄까지,
    그 사이 문맥 줄 포함)입니다.
    `codes`(예: {"I001", "E711"})만 고칩니다. 파일 전체가 diff에 보이지 않으면
    F·I 규칙은 고치지 않습니다(run_ruff와 같은 기준). 고칠 것이 없으면 빈 목록.
    """
    settings = settings or review_settings.DEFAULT_SETTINGS
    text, complete = reconstruct_file(file)
    if not complete:
        codes = {c for c in codes if not c.startswith(("F", "I"))}
    if not text or not codes:
        return []
    config_args, cwd = _ruff_base(settings, file.path)
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        *config_args,
        "--select",
        ",".join(sorted(codes)),
        "--fix",
        "--unsafe-fixes",
        "--diff",
        "--stdin-filename",
        file.path,
        "-",
    ]
    result = subprocess.run(
        cmd,
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
    )
    hunks: list[tuple[int, list[str]]] = []  # (hunk 시작 원래 줄, 본문 줄들)
    for raw in (result.stdout or "").splitlines():
        m = _HUNK_RE.match(raw)
        if m:
            hunks.append((int(m.group(1)), []))
        elif hunks and not raw.startswith(("---", "+++")):
            hunks[-1][1].append(raw)
    blocks: list[tuple[int, int, list[str]]] = []
    for start, body in hunks:
        changed = [i for i, r in enumerate(body) if r[:1] in {"-", "+"}]
        if not changed:
            continue
        first, last = changed[0], changed[-1]
        old_no = start + sum(1 for r in body[:first] if r[:1] != "+")
        old_end = old_no - 1 + sum(1 for r in body[first : last + 1] if r[:1] != "+")
        new = [r[1:] for r in body[first : last + 1] if r[:1] != "-"]
        blocks.append((old_no, max(old_end, old_no), new))
    return blocks
