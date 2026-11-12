"""1번 조사: 10번 diff를 gpt-oss로 3회 리뷰해 IndentationError 오탐 반복 여부와
리뷰어 입력(numbered_text)을 기록한다. 코드 수정 없음."""

import ast
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
# 프로젝트 루트 (이 파일: evaluation/notes/case10/case10_probe.py)
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src import diff_parser as dp  # noqa: E402
from src.agent import run_query  # noqa: E402
from src.guardrails import check_input  # noqa: E402
from src.reviewers import _task_message  # noqa: E402
from src.tools import reconstruct_file  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
text = (config.EVAL_DIFFS_DIR / "10_clean_refactor.diff").read_text(encoding="utf-8")
chk = check_input(text)
files = [f.path for f in chk.py_files]
task = _task_message(dp.numbered_text(chk.py_files), files)
(OUT / "reviewer_input.txt").write_text(task, encoding="utf-8")

# 변경 후 파일이 실제로 파싱되는지 (오탐 판정의 기준)
for f in chk.py_files:
    src, complete = reconstruct_file(f)
    try:
        ast.parse(src)
        parsed = "OK"
    except SyntaxError as exc:
        parsed = f"SyntaxError: {exc}"
    print(f"[ast] {f.path} complete={complete} parse={parsed}")
    (OUT / "reconstructed_after.py").write_text(src, encoding="utf-8")

rows = []
for i in range(1, 4):
    t = time.time()
    out = run_query("data/eval_diffs/10_clean_refactor.diff 를 리뷰해줘")
    secs = round(time.time() - t, 1)
    review = next((e["review"] for e in out["trace"] if e["type"] == "review"), None)
    reviewers = [
        {k: e[k] for k in ("name", "findings", "structured")}
        for e in out["trace"]
        if e["type"] == "reviewer"
    ]
    errors = [e["error"] for e in out["trace"] if e["type"] == "error"]
    findings = review["findings"] if review else []
    rows.append(
        {
            "run": i,
            "seconds": secs,
            "reviewers": reviewers,
            "errors": errors,
            "findings": findings,
            "summary": review["summary"] if review else None,
        }
    )
    print(f"\n=== run {i} ({secs}s) reviewers={reviewers} errors={errors}")
    for f in findings:
        print(
            f"  - {f['issue_type']}/{f['severity']} {f['file']}:{f['line']} "
            f"{f['message'][:200]}"
        )
        refs = [e.get("source") or e.get("rule_id") for e in f["evidence"]]
        print(f"    evidence={refs}")
    (OUT / f"run{i}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

(OUT / "summary.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
)
