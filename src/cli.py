"""CLI 진입점.

    python -m src.cli review <diff 파일> [--verbose] [--batch] [--repo owner/repo]
                             [--repo-dir 저장소 폴더] [--max-lines N]

- 기본(대화형): 리뷰를 출력한 뒤 "PR에 코멘트로 게시할까요?"를 묻고(HITL),
  답에 따라 같은 thread_id로 재개합니다.
  게시는 승인 + 허용 목록 저장소일 때만, 이번 범위는 dry-run.
- `--batch`: 승인 질문(HITL)까지 실행해 승인 대기 상태를 trace에 기록하고
  **재개하지 않습니다.** 결과(answer·contexts·trace)를 JSON으로 출력합니다.
  평가 실행기(run_eval.py)와 같은 동작입니다.
- `--verbose`: 내부 로그(가드레일·planner·도구 호출·출력 검증)와
  고치는 방법 전체를 붙입니다.
- `--repo-dir`: 리뷰 대상 로컬 저장소 폴더. 그 저장소에 ruff 설정(ruff.toml·
  .ruff.toml·pyproject.toml [tool.ruff])이 있으면 그 기준으로 검사하고,
  `pyproject.toml [tool.citereview] max-lines`가 있으면 변경 한도로 씁니다.
  (`--repo`는 GitHub 게시 대상 owner/repo라 이름을 나눴습니다.)
- `--max-lines`: 변경 줄 수 한도(1~500). 지정하면 저장소 설정보다 우선합니다.
  둘 다 없으면 기본 300줄 + CiteReview ruff.toml (평가와 같은 기준).
"""

from __future__ import annotations

import argparse
import json
import sys

from src.agent import resume_review, run_query
from src.config import (
    MAX_LINES_CAP,
    MAX_PY_CHANGED_LINES,
    REVIEW_MODEL_KEY,
    REVIEW_MODELS,
    get_llm,
    review_model_region,
)
from src.render import render_verbose

APPROVAL_HELP = "[y] 승인  [n] 거절  [i 번호...] 그 항목을 빼고 승인 (예: i 2 3)"


def parse_decision(answer: str, repo: str | None) -> dict:
    """사람의 입력을 HITL 결정으로 바꿉니다. 알아들을 수 없는 입력은 거절로 봅니다."""
    words = answer.strip().lower().split()
    if words[:1] == ["y"]:
        return {"action": "approve", "ignore": [], "repo": repo}
    if words[:1] == ["i"]:
        ignore = [int(w) for w in words[1:] if w.isdigit()]
        return {"action": "approve", "ignore": ignore, "repo": repo}
    return {"action": "reject", "ignore": [], "repo": repo}


def main(argv: list[str] | None = None) -> int:
    """명령줄 인자를 읽어 리뷰를 실행하고 결과를 출력합니다."""
    parser = argparse.ArgumentParser(
        prog="citereview", description="근거를 다는 코드 리뷰"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review", help="diff 파일을 리뷰합니다")
    review.add_argument("diff", help="리뷰할 diff 파일 경로 (git diff 형식)")
    review.add_argument("--verbose", action="store_true", help="내부 로그도 출력")
    review.add_argument(
        "--batch", action="store_true", help="승인 대기까지만 실행하고 JSON 출력"
    )
    review.add_argument("--repo", help="게시 대상 저장소 owner/repo (허용 목록만 가능)")
    review.add_argument(
        "--repo-dir", help="리뷰 대상 로컬 저장소 폴더 (그 저장소의 ruff 설정을 따름)"
    )
    review.add_argument(
        "--review-model",
        choices=sorted(REVIEW_MODELS),
        default=REVIEW_MODEL_KEY,
        help=f"리뷰 모델 (기본 {REVIEW_MODEL_KEY})",
    )
    review.add_argument(
        "--max-lines",
        type=int,
        help=f"변경 줄 수 한도 (1~{MAX_LINES_CAP}, 기본 {MAX_PY_CHANGED_LINES})",
    )
    args = parser.parse_args(argv)
    if args.max_lines is not None and not 1 <= args.max_lines <= MAX_LINES_CAP:
        parser.error(f"--max-lines는 1~{MAX_LINES_CAP} 사이여야 합니다")

    # Windows 콘솔에서도 한글이 깨지지 않게 UTF-8로 입출력
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    model_id = REVIEW_MODELS[args.review_model]
    result = run_query(
        f"{args.diff} 를 리뷰해줘",
        llm=get_llm(model_id),
        repo_dir=args.repo_dir,
        max_lines=args.max_lines,
    )
    if args.batch:
        result["review_model"] = {
            "key": args.review_model,
            "id": model_id,
            "region": review_model_region(model_id),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    region = review_model_region(model_id)
    print(f"리뷰 모델: {args.review_model} ({model_id}, {region})")
    print(result["answer"])
    if args.verbose:
        print(render_verbose(result["trace"]))
    waiting = [
        e for e in result["trace"] if e["type"] == "hitl" and e["status"] == "waiting"
    ]
    if not waiting:  # 거절·실패한 리뷰는 게시할 것이 없음
        return 0

    print(f"\nPR에 코멘트로 게시할까요? {APPROVAL_HELP}")
    decision = parse_decision(input("> "), args.repo)
    outcome = resume_review(waiting[0]["thread_id"], decision)["post"]
    if outcome["status"] == "posted":
        print(f"\n[dry-run] {outcome['repo']}에 게시할 코멘트:\n")
        print(outcome["body"])
    else:
        print(f"\n게시하지 않았습니다: {outcome['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
