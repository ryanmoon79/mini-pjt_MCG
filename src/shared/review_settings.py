"""리뷰 한 건에 적용되는 기준: 변경 줄 수 한도, 저장소 ruff 설정, 출력 형식 재생성 횟수.

여러 도메인(review·tools·guardrails·interfaces)이 함께 읽는 설정이라 shared에 둡니다.
아무것도 지정하지 않으면(평가 실행 포함) 항상 기본값입니다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from src.shared.config import ROOT_DIR, RUFF_CONFIG_PATH

# ── 입력 범위 (SERVICE.md 4번 정책 4) ───────────────────────────────
MAX_PY_CHANGED_LINES = 300  # .py 추가·삭제 줄 합계 상한 (기본값, 평가는 항상 이 값)


MAX_LINES_CAP = 500  # 사용자 설정(CLI·GUI·저장소)으로 올릴 수 있는 최대값


MAX_FORMAT_RETRIES = 1  # 출력 형식 오류 시 재생성 횟수


# 선택 작업 C2: PEP 8 근거 style 지적은 그 줄에 연결표상 대응 ruff 위반이 있을 때만 유지
PEP8_CROSS_CHECK = True


# 버그 리뷰어 프롬프트의 "실패 경로" 규칙(round4 준비, 커밋 542b0bb)을 넣을지.
# 기본은 넣음. 재현율 비교 실험(round4 이후, day07 규칙 있음/없음)에서만 코드로
# 잠시 False로 바꿔 돌립니다 — 평가·CLI·GUI에는 옵션을 두지 않습니다.
BUG_FAILURE_PATH_RULE = True


# 리뷰어에게 넘기는 diff의 문맥 (round5 이후 개선 1-c, 실험 중).
# "full"(기본): 받은 diff 그대로(-U9999면 파일 전체).
# "focused": 바뀐 줄이 든 함수·클래스 + import + 참조하는 모듈 상수만
#   (`diff.parser.focused_numbered_text`). 측정 실험에서만 코드로 잠시 바꿉니다.
REVIEWER_DIFF_CONTEXT = "full"


# ── 리뷰 설정: 변경 줄 수 한도와 ruff 기준 (저장소 설정 따르기) ─────────────


@dataclass(frozen=True)
class ReviewSettings:
    """리뷰 한 건에 적용되는 기준.

    아무것도 지정하지 않으면(평가 실행 포함) 항상 기본값입니다:
    변경 한도 300줄, CiteReview `ruff.toml`(줄 길이 88칸).
    """

    max_lines: int = MAX_PY_CHANGED_LINES
    max_lines_source: str = "기본값"
    ruff_config: Path = RUFF_CONFIG_PATH
    ruff_cwd: Path = ROOT_DIR
    line_length: int = 88
    ruff_source: str = "CiteReview ruff.toml"

    def label(self) -> str:
        """화면 안내 줄·요약 4줄에 쓰는 적용 기준 문구."""
        return (
            f"줄 길이 {self.line_length}칸({self.ruff_source}), "
            f"변경 한도 {self.max_lines}줄({self.max_lines_source})"
        )


DEFAULT_SETTINGS = ReviewSettings()


def _read_toml(path: Path) -> dict:
    import tomllib

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _repo_ruff(repo: Path) -> tuple[Path, int, str] | None:
    """저장소 자체 ruff 설정 (ruff.toml → .ruff.toml → pyproject.toml [tool.ruff]).

    돌려주는 값: (설정 파일, line-length, 표시 이름). 없으면 None.
    line-length를 적지 않은 설정은 ruff 기본값 88입니다.
    """
    for name in ("ruff.toml", ".ruff.toml"):
        path = repo / name
        if path.is_file():
            data = _read_toml(path)
            return path, int(data.get("line-length", 88)), f"저장소 {name}"
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        ruff = _read_toml(pyproject).get("tool", {}).get("ruff")
        if isinstance(ruff, dict):
            length = int(ruff.get("line-length", 88))
            return pyproject, length, "저장소 pyproject.toml [tool.ruff]"
    return None


def load_review_settings(
    repo_dir: str | Path | None = None, max_lines: int | None = None
) -> ReviewSettings:
    """적용할 리뷰 기준을 정합니다.

    - 변경 한도: CLI·GUI 지정 > 저장소 `pyproject.toml [tool.citereview] max-lines`
      > 기본 300. 최대 500
      (CLI·GUI가 500을 넘기면 ValueError, 저장소 값은 500으로 자름).
    - ruff 기준: 저장소 경로가 있고 그 저장소에 ruff 설정이 있으면 그 설정,
      아니면 CiteReview `ruff.toml`. diff 파일만 받은 경우(평가 포함)는 항상 기본값.
    """
    if max_lines is not None and not 1 <= int(max_lines) <= MAX_LINES_CAP:
        raise ValueError(
            f"변경 한도는 1~{MAX_LINES_CAP}줄 사이여야 합니다: {max_lines}"
        )
    settings = DEFAULT_SETTINGS
    repo = Path(repo_dir) if repo_dir else None
    if repo is not None and repo.is_dir():
        found = _repo_ruff(repo)
        if found:
            path, length, source = found
            settings = replace(
                settings,
                ruff_config=path,
                ruff_cwd=repo,
                line_length=length,
                ruff_source=source,
            )
        repo_max = (
            _read_toml(repo / "pyproject.toml")
            .get("tool", {})
            .get("citereview", {})
            .get("max-lines")
        )
        if max_lines is None and isinstance(repo_max, int) and repo_max > 0:
            capped = min(repo_max, MAX_LINES_CAP)
            source = "저장소 설정" + (
                f", {repo_max}→{capped}" if capped != repo_max else ""
            )
            settings = replace(settings, max_lines=capped, max_lines_source=source)
    if max_lines is not None:
        settings = replace(
            settings, max_lines=int(max_lines), max_lines_source="사용자 지정"
        )
    return settings
