"""RAGAS 4개 지표 계산기 (evaluation/README.md 4절).

사용법 (평가 전용 환경 .venv-eval, Python 3.12):
    .venv-eval/Scripts/python.exe evaluation/run_ragas.py <실행ID>
    .venv-eval/Scripts/python.exe evaluation/run_ragas.py <실행ID> --cases 3,4

    .venv-eval/Scripts/python.exe evaluation/run_ragas.py <ID> --metrics faithfulness

- `src/`를 import하지 않습니다. `evaluation/runs/<실행ID>/<케이스>/`에 저장된
  `output.json`(answer·contexts, 마스킹 후), `reference.json`(코퍼스 청크 원문),
  `diff_hunks.txt`(마스킹된 diff), `ruff.json`(리뷰 중 ruff_check 출력)만 읽습니다.
- **faithfulness의 contexts = 검색 청크 + diff hunk + ruff 출력.**
  리뷰는 diff와 도구 출력을 근거로 쓰므로, 코퍼스 청크만 넣으면
  diff에 대한 문장이 모두 "근거 없음"이 됩니다.
  `--faithfulness-contexts corpus`로 검색 청크만 쓰는 기존 설정도 재현할 수 있습니다.
- context_precision·context_recall의 contexts는 **검색 청크만**입니다 (검색 품질 지표).
  정답 청크의 검색 순위(contexts 목록 순서 기준)를 LLM 없이 함께 계산합니다.
- `--metrics`로 일부 지표만 다시 계산하면
  기존 `ragas.json`의 나머지 지표 값은 유지합니다.
- 대상은 **본 세트(1~20번)**입니다. 추가 세트(21·22번)는 완성 기준과 같은 이유로 뺍니다.
- 계산 순서: ① `faithfulness`·`answer_relevancy` (contexts가 있는 케이스)
  ② `context_precision`·`context_recall` (reference가 있는 케이스).
  ②가 실패하면 결과 파일에 "미측정 + 사유"로 남기고 ①의 값은 그대로 저장합니다.
- 판정 LLM은 Nova Pro, 임베딩은 Titan v2 (Bedrock, us-east-1).
  리뷰 모델과 다른 모델을 써서 자기 평가 편향을 줄입니다.
  이 두 모델은 이 파일 안에서만 만듭니다 (CLAUDE.md 4번).
- 결과: `evaluation/runs/<실행ID>/ragas.json`
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "evaluation" / "runs"
MAIN_SET_MAX_ID = 20  # 본 세트 1~20번 (21번부터는 추가 세트)

JUDGE_MODEL_ID = "us.amazon.nova-pro-v1:0"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
# 역할별 리전 (src/config.py의 JUDGE_REGION·EMBEDDING_REGION과 같은 값으로 유지 —
# 이 파일은 .venv-eval에서 돌아 src를 import하지 않음). `.env` 리전은 읽지 않음
JUDGE_REGION = "us-east-1"
EMBEDDING_REGION = "us-east-1"
ALL_METRICS = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
)


def load_samples(run_dir: Path, wanted: set[str] | None) -> list[dict]:
    """실행 기록에서 본 세트 케이스의 입력·답변·검색 청크·reference를 읽습니다."""
    samples = []
    for d in sorted(run_dir.iterdir(), key=lambda p: (len(p.name), p.name)):
        if not d.is_dir() or not d.name.isdigit():
            continue
        if int(d.name) > MAIN_SET_MAX_ID or (wanted and d.name not in wanted):
            continue
        out_path, ref_path = d / "output.json", d / "reference.json"
        if not out_path.exists():
            continue
        out = json.loads(out_path.read_text(encoding="utf-8"))
        ref = (
            json.loads(ref_path.read_text(encoding="utf-8"))
            if ref_path.exists()
            else {"rule_ids": [], "reference": []}
        )
        chunks = [c["text"] for c in out["contexts"]]
        samples.append(
            {
                "id": d.name,
                "user_input": out["input"],
                "response": out["answer"],
                "reviewed": "## 요약" in out["answer"],  # 거절 응답 제외용
                "retrieved_contexts": chunks,
                "full_contexts": chunks + diff_and_ruff_contexts(d),
                "context_rule_ids": [c["rule_id"] for c in out["contexts"]],
                "reference": "\n\n".join(ref["reference"]),
                "reference_rule_ids": ref["rule_ids"],
            }
        )
    return samples


def diff_and_ruff_contexts(d: Path) -> list[str]:
    """faithfulness용 추가 contexts: 마스킹된 diff hunk와 리뷰 중 ruff_check 출력."""
    extra = []
    hunks = d / "diff_hunks.txt"
    if hunks.exists():
        extra.append(
            "리뷰 대상 diff (마스킹 후):\n" + hunks.read_text(encoding="utf-8")
        )
    ruff_path = d / "ruff.json"
    if ruff_path.exists():
        rec = json.loads(ruff_path.read_text(encoding="utf-8"))
        if rec["called_files"]:
            body = (
                json.dumps(rec["findings"], ensure_ascii=False)
                if rec["findings"]
                else "위반 없음"
            )
            files = ", ".join(rec["called_files"])
            extra.append(f"ruff_check 출력 ({files}):\n{body}")
    return extra


def reference_ranks(samples: list[dict]) -> dict[str, dict]:
    """정답 청크(reference rule_id)의 검색 순위 (LLM 없음, context_precision 보조 근거).

    순위는 RAGAS에 넣은 contexts 목록 순서 기준(1부터)이며, 없으면 None입니다.
    """
    ranks = {}
    for s in samples:
        if not s["reference_rule_ids"]:
            continue
        ids = s["context_rule_ids"]
        ranks[s["id"]] = {
            "ranks": {
                r: (ids.index(r) + 1 if r in ids else None)
                for r in s["reference_rule_ids"]
            },
            "contexts": len(ids),
        }
    return ranks


def build_judges():
    """판정 LLM(Nova Pro)과 임베딩(Titan v2)을 RAGAS 래퍼로 감싸 돌려줍니다."""
    from botocore.config import Config
    from dotenv import load_dotenv
    from langchain_aws import BedrockEmbeddings, ChatBedrockConverse
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    load_dotenv(ROOT / ".env", override=False)
    retry = Config(retries={"max_attempts": 10, "mode": "adaptive"})
    llm = ChatBedrockConverse(
        model=JUDGE_MODEL_ID, region_name=JUDGE_REGION, temperature=0, config=retry
    )
    emb = BedrockEmbeddings(
        model_id=EMBEDDING_MODEL_ID, region_name=EMBEDDING_REGION, config=retry
    )
    return LangchainLLMWrapper(llm), LangchainEmbeddingsWrapper(emb)


def score(
    samples: list[dict], metrics: list, llm, emb, ctx_key: str = "retrieved_contexts"
) -> dict[str, dict]:
    """RAGAS를 돌려 {케이스 id: {지표: 값}}을 돌려줍니다 (NaN은 None).

    `ctx_key`: RAGAS `retrieved_contexts`로 넣을 필드 (검색 청크만 / 전체 contexts).
    """
    from ragas import EvaluationDataset, evaluate
    from ragas.run_config import RunConfig

    fields = ("user_input", "response", "retrieved_contexts", "reference")
    rows_in = []
    for s in samples:
        row = dict(s, retrieved_contexts=s[ctx_key])
        rows_in.append({k: row[k] for k in fields if row.get(k)})
    dataset = EvaluationDataset.from_list(rows_in)
    result = evaluate(
        dataset,
        metrics=metrics,
        llm=llm,
        embeddings=emb,
        run_config=RunConfig(max_workers=2, timeout=240, max_retries=5),
        show_progress=False,
    )
    rows = result.to_pandas().to_dict(orient="records")
    names = [m.name for m in metrics]
    per_case = {}
    for s, row in zip(samples, rows):
        per_case[s["id"]] = {
            n: (None if row.get(n) is None or math.isnan(row[n]) else round(row[n], 4))
            for n in names
        }
    return per_case


def mean(per_case: dict[str, dict], name: str) -> dict:
    """지표 평균과 계산된 건수 (NaN·실패 케이스는 평균에서 빼고 건수로 보고)."""
    vals = [v[name] for v in per_case.values() if v.get(name) is not None]
    return {
        "mean": round(sum(vals) / len(vals), 4) if vals else None,
        "scored": len(vals),
        "targets": len(per_case),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CiteReview RAGAS 4개 지표")
    parser.add_argument("run_id", help="evaluation/runs/ 아래 실행 ID")
    parser.add_argument("--cases", help="쉼표로 구분한 케이스 id (예: 3,4)")
    parser.add_argument(
        "--metrics",
        default=",".join(ALL_METRICS),
        help="다시 계산할 지표 (쉼표 구분). 나머지는 기존 ragas.json 값 유지",
    )
    parser.add_argument(
        "--faithfulness-contexts",
        choices=["full", "corpus"],
        default="full",
        help="full=검색 청크+diff hunk+ruff 출력(기본), corpus=검색 청크만(기존 설정)",
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    run_dir = RUNS_DIR / args.run_id
    wanted = {c.strip() for c in args.cases.split(",")} if args.cases else None
    samples = load_samples(run_dir, wanted)
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        context_recall,
        faithfulness,
    )

    wanted_metrics = {m.strip() for m in args.metrics.split(",") if m.strip()}
    out_path = run_dir / "ragas.json"
    report: dict = (
        json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    )
    report.update({"run_id": args.run_id, "judge_model": JUDGE_MODEL_ID})
    per_case: dict = report.get("per_case", {})
    llm, emb = build_judges()

    # ① faithfulness: 리뷰가 끝난 케이스 (거절 응답 제외).
    #    contexts = 검색 청크 + diff hunk + ruff 출력 (corpus 설정이면 검색 청크만)
    if "faithfulness" in wanted_metrics:
        full = args.faithfulness_contexts == "full"
        key = "full_contexts" if full else "retrieved_contexts"
        targets = [s for s in samples if s["reviewed"] and s[key]]
        res = score(targets, [faithfulness], llm, emb, ctx_key=key) if targets else {}
        for cid, vals in res.items():
            per_case.setdefault(cid, {}).update(vals)
        report["faithfulness_contexts"] = args.faithfulness_contexts
        report["faithfulness_targets"] = [s["id"] for s in targets]
        report["faithfulness"] = mean(res, "faithfulness")

    # ② answer_relevancy: 검색 청크가 있는 케이스 (contexts를 쓰지 않는 지표)
    first = [s for s in samples if s["retrieved_contexts"]]
    if "answer_relevancy" in wanted_metrics:
        res = score(first, [answer_relevancy], llm, emb) if first else {}
        for cid, vals in res.items():
            per_case.setdefault(cid, {}).update(vals)
        report["first_targets"] = [s["id"] for s in first]
        report["answer_relevancy"] = mean(res, "answer_relevancy")

    # ③ context_precision·context_recall: reference가 있는 케이스, 검색 청크만
    second = [s for s in samples if s["reference"] and s["retrieved_contexts"]]
    report["second_targets"] = [s["id"] for s in second]
    report["second_no_contexts"] = [
        s["id"] for s in samples if s["reference"] and not s["retrieved_contexts"]
    ]
    report["reference_ranks"] = reference_ranks(samples)  # LLM 없음
    ctx_metrics = [
        m for m in (context_precision, context_recall) if m.name in wanted_metrics
    ]
    if ctx_metrics:
        try:
            res = score(second, ctx_metrics, llm, emb)
            for m in ctx_metrics:
                report[m.name] = mean(res, m.name)
        except Exception as exc:  # 결과 기록: 미측정 + 사유
            reason = f"{type(exc).__name__}: {str(exc)[:200]}"
            res = {}
            for m in ctx_metrics:
                report[m.name] = {"mean": None, "unmeasured": reason}
        for cid, vals in res.items():
            per_case.setdefault(cid, {}).update(vals)
    report["per_case"] = per_case

    (run_dir / "ragas.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "per_case"},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"저장: {run_dir / 'ragas.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
