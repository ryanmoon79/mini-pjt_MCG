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

from pathlib import Path

# ── 모델 ────────────────────────────────────────────────────────────
# 리뷰 모델: AGENTS.md 고정값(Sonnet 4.5)에서 사용자 결정으로 Haiku 4.5로 변경
# (CLAUDE.md 1번).
REVIEW_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"  # 1024차원
AWS_REGION = "us-east-1"
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
MAX_PY_CHANGED_LINES = 300  # .py 파일의 추가·삭제 줄 합계 상한
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


def get_llm():
    """리뷰 모델(Bedrock `ChatBedrockConverse`)을 만들어 돌려줍니다.

    호출할 때마다 새 객체를 만듭니다. 그래프·노드 생성 함수는 `llm` 인자가
    `None`일 때만 이 함수를 부르므로, 테스트에서는 실제 모델이 만들어지지 않습니다.
    """
    load_env()
    from langchain_aws import ChatBedrockConverse

    return ChatBedrockConverse(
        model=REVIEW_MODEL_ID,
        region_name=AWS_REGION,
        temperature=TEMPERATURE,
        config=bedrock_client_config(),
    )


def get_embeddings():
    """검색용 임베딩 모델(Bedrock Titan v2)을 만들어 돌려줍니다."""
    load_env()
    from langchain_aws import BedrockEmbeddings

    return BedrockEmbeddings(
        model_id=EMBEDDING_MODEL_ID,
        region_name=AWS_REGION,
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
