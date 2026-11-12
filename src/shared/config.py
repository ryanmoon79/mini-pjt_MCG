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
# 프로젝트 루트 (이 파일: src/shared/config.py → 세 단계 위)
ROOT_DIR = Path(__file__).resolve().parents[2]


ENV_PATH = ROOT_DIR / ".env"


CORPUS_DIR = ROOT_DIR / "data" / "corpus"


EVAL_DIFFS_DIR = ROOT_DIR / "data" / "eval_diffs"


EVALUATION_DIR = ROOT_DIR / "evaluation"


RUNS_DIR = EVALUATION_DIR / "runs"


RUFF_CONFIG_PATH = ROOT_DIR / "ruff.toml"


# ── 저장소 (AGENTS.md 고정값) ───────────────────────────────────────
CHROMA_PERSIST_DIR = "./chroma_db"


CHROMA_COLLECTION = "sds_policies"


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
