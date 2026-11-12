"""CiteReview 설정: 모델 ID·경로 상수와 모델 생성 함수.

규칙 (AGENTS.md, CLAUDE.md 4번):
- 이 모듈을 import해도 **실제 모델을 만들지 않습니다.**
  모델은 `get_llm()`·`get_embeddings()`를 호출할 때만 만들어집니다.
  그래프·노드는 `llm=None`을 받아 주입할 수 있게 하고, `None`일 때만
  이 함수들을 부릅니다. 테스트는 가짜 모델을 주입합니다.
- 비밀값(AWS 키 등)은 `.env`에서 읽고 코드에 적지 않습니다.
- RAGAS 판정 LLM(Nova Pro)은 여기서 만들지 않습니다(평가 스크립트 안에서만 생성).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

# ── 모델 ────────────────────────────────────────────────────────────
# 리뷰 모델: AGENTS.md 고정값(Sonnet 4.5) → Haiku 4.5 → gpt-oss-120b → Sonnet 4.6
# (모두 사용자 결정, CLAUDE.md 1번). 앞의 모델들이 계정 일일 토큰 한도에 걸려
# 개발·평가가 막혔기 때문. gpt-oss는 답에 추론 블록이 섞여 content가 목록으로 옵니다.
REVIEW_MODELS = {
    "sonnet": "us.anthropic.claude-sonnet-4-6",  # 현재 기본값 (us-west-1)
    "gpt-oss": "openai.gpt-oss-120b-1:0",  # 3차 조건 B
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",  # 1·2차 평가, 3차 조건 A
}
REVIEW_MODEL_KEY = "sonnet"
REVIEW_MODEL_ID = REVIEW_MODELS[REVIEW_MODEL_KEY]
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"  # 1024차원
# ── 리전 (역할별, 코드 상수) ─────────────────────────────────────────
# 모든 Bedrock 호출은 아래 값을 region_name으로 직접 넘깁니다. 그래서 `.env`의
# AWS_DEFAULT_REGION은 읽지 않습니다(있어도 동작이 바뀌지 않음).
AWS_REGION = "us-east-1"  # 리뷰 모델 리전의 기본값 (REVIEW_MODEL_REGIONS에 없는 모델)
REVIEW_MODEL_REGIONS = {REVIEW_MODELS["sonnet"]: "us-west-1"}  # 리뷰 모델별 리전
EMBEDDING_REGION = "us-east-1"  # 검색 임베딩 (Titan v2)
JUDGE_REGION = "us-east-1"  # 평가 판정 LLM (Nova Pro, run_eval.py)
TEMPERATURE = 0

# ── 경로 ────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
CORPUS_DIR = ROOT_DIR / "data" / "corpus"
EVAL_DIFFS_DIR = ROOT_DIR / "data" / "eval_diffs"
EVALUATION_DIR = ROOT_DIR / "evaluation"
RUNS_DIR = EVALUATION_DIR / "runs"
RUFF_CONFIG_PATH = ROOT_DIR / "ruff.toml"

# ── 저장소 (AGENTS.md 고정값) ───────────────────────────────────────
CHROMA_PERSIST_DIR = "./chroma_db"
CHROMA_COLLECTION = "sds_policies"

# ── 입력 범위 (SERVICE.md 4번 정책 4) ───────────────────────────────
MAX_PY_CHANGED_LINES = 300  # .py 추가·삭제 줄 합계 상한 (기본값, 평가는 항상 이 값)
MAX_LINES_CAP = 500  # 사용자 설정(CLI·GUI·저장소)으로 올릴 수 있는 최대값
MAX_FORMAT_RETRIES = 1  # 출력 형식 오류 시 재생성 횟수
# 선택 작업 C2: PEP 8 근거 style 지적은 그 줄에 연결표상 대응 ruff 위반이 있을 때만 유지
PEP8_CROSS_CHECK = True


def load_env() -> None:
    """프로젝트 루트의 `.env`를 환경변수로 읽어 들입니다.

    이미 설정된 환경변수는 덮어쓰지 않습니다. `.env`가 없으면 아무것도 하지 않습니다
    (테스트 환경처럼 실제 모델을 쓰지 않는 경우).
    """
    from dotenv import load_dotenv

    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)


def bedrock_client_config():
    """Bedrock 클라이언트 설정: 호출 한도(Throttling) 오류를 더 오래 재시도."""
    from botocore.config import Config

    return Config(retries={"max_attempts": 10, "mode": "adaptive"}, read_timeout=120)


def get_llm(model_id: str | None = None):
    """리뷰 모델(Bedrock `ChatBedrockConverse`)을 만들어 돌려줍니다.

    호출할 때마다 새 객체를 만듭니다. 그래프·노드 생성 함수는 `llm` 인자가
    `None`일 때만 이 함수를 부르므로, 테스트에서는 실제 모델이 만들어지지 않습니다.
    `model_id`가 없으면 `REVIEW_MODEL_ID`(Sonnet 4.6, us-west-1). CLI·평가 실행기는
    `--review-model`로 다른 모델(`REVIEW_MODELS`)을 고릅니다.
    리전은 `review_model_region`으로 모델마다 정합니다.
    """
    load_env()
    from langchain_aws import ChatBedrockConverse

    model_id = model_id or REVIEW_MODEL_ID
    extra = {}
    if model_id.startswith("openai.gpt-oss"):
        # langchain_aws는 gpt-oss의 tool_choice 지원을 모른다고 보고, create_agent의
        # 구조화 출력(ToolStrategy)이 넘기는 tool_choice="any"를 거절합니다.
        # Bedrock 서버는 이 값을 받아 주므로 지원 목록을 직접 적습니다
        # (단, 강제력은 없어 모델이 결과 도구를 안 부를 수 있음 → trace의 reviewer
        # 이벤트 `structured`로 확인). Claude는 langchain_aws가 스스로 판단(2차와 같음).
        extra["supports_tool_choice_values"] = ("auto", "any", "tool")
    return ChatBedrockConverse(
        model=model_id,
        region_name=review_model_region(model_id),
        temperature=TEMPERATURE,
        config=bedrock_client_config(),
        **extra,
    )


def review_model_region(model_id: str | None = None) -> str:
    """리뷰 모델의 Bedrock 리전 (Sonnet 4.6은 us-west-1, 나머지는 AWS_REGION)."""
    return REVIEW_MODEL_REGIONS.get(model_id or REVIEW_MODEL_ID, AWS_REGION)


def get_embeddings():
    """검색용 임베딩 모델(Bedrock Titan v2)을 만들어 돌려줍니다."""
    load_env()
    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(
        model_id=EMBEDDING_MODEL_ID,
        region_name=EMBEDDING_REGION,
        config=bedrock_client_config(),
    )


def allowed_post_repos() -> set[str]:
    """게시 허용 목록 (`.env`의 CITEREVIEW_ALLOWED_POST_REPOS, 쉼표 구분 owner/repo).

    게시 대상은 이 목록만으로 정합니다. LLM 출력이나 diff 내용은 읽지 않습니다
    (SERVICE.md 4번 정책 3·5의 "코드로 강제").
    """
    import os

    load_env()
    raw = os.environ.get("CITEREVIEW_ALLOWED_POST_REPOS", "")
    return {r.strip() for r in raw.split(",") if r.strip()}


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
