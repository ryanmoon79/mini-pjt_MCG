"""인-아웃 세트 CSV 검증 (evaluation/README.md 1절).

사용법:
    python evaluation/validate_test_queries.py

- **건수·비율 검사는 본 세트(id 1~20)만** 셉니다: 카테고리 8/4/5/3,
  positive의 issue_type 구성(bug 3 / style 3 / security 2), 하드코딩 비밀값 1건 이하.
- **형식 검사는 모든 행**(본 세트 + 추가 세트 21번~)에 적용합니다: 7열, id 연속,
  rule_id 체계, `expected_tools`에 `github_*` 없음, input 형식, diff 파일 존재.
- 표준 라이브러리(csv)만 씁니다. 모델을 부르지 않습니다.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "evaluation" / "test_queries.csv"
DEP_MAP = ROOT / "data" / "corpus" / "deprecated_map.md"
PEP8_DOC = ROOT / "data" / "corpus" / "pep8.md"
MAIN_SET_MAX_ID = 20
COLUMNS = [
    "id",
    "category",
    "input",
    "expected_traits",
    "forbidden",
    "expected_tools",
    "note",
]
ALLOWED_TOOLS = {"ruff_check", "search_evidence"}
RUFF_OK = re.compile(
    r"^ruff:(E501|E401|E402|I001|N8\d\d|E711|E712|E721|E722|E731|W291|W293|F\d{3})$"
)
SCAN_OK = re.compile(r"^(secret|pii|injection)-scanner:[a-z0-9-]+$")
TOKEN_RE = re.compile(
    r"\b(?:DEP-[A-Za-z_]+|PEP8-[a-z-]+|CONV-\d+|LC-[A-Za-z0-9-]+|ruff:[A-Z]\d+"
    r"|(?:secret|pii|injection)-scanner:[a-z0-9-]+)"
)
INPUT_RE = re.compile(r"data/eval_diffs/\d{2}_\w+\.diff 를 리뷰해줘")


def main() -> int:
    """검사 결과를 한 줄씩 출력하고, 하나라도 실패하면 1을 돌려줍니다."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= bool(cond)
        print(
            ("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else "")
        )

    with CSV_PATH.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames
        rows = list(reader)
    main_set = [r for r in rows if int(r["id"]) <= MAIN_SET_MAX_ID]
    extra = [r for r in rows if int(r["id"]) > MAIN_SET_MAX_ID]
    print(f"     전체 {len(rows)}행 = 본 세트 {len(main_set)} + 추가 세트 {len(extra)}")

    # ── 형식 (모든 행) ──
    check("컬럼 7개·순서", header == COLUMNS, str(header))
    check(
        "모든 행 7열",
        all(None not in r and len(r) == 7 for r in rows),
    )
    ids = [int(r["id"]) for r in rows]
    check("id 1부터 연속", ids == list(range(1, len(rows) + 1)), str(ids))

    # ── 건수·비율 (본 세트만) ──
    check("본 세트 20건", len(main_set) == 20, f"{len(main_set)}건")
    cnt: dict[str, int] = {}
    for r in main_set:
        cnt[r["category"]] = cnt.get(r["category"], 0) + 1
    expected = {"positive": 8, "negative": 4, "edge": 5, "guardrail": 3}
    check("본 세트 카테고리 8/4/5/3", cnt == expected, str(cnt))
    pos = [r for r in main_set if r["category"] == "positive"]
    types: dict[str, int] = {"bug": 0, "style": 0, "security": 0, "?": 0}
    for r in pos:
        found = [
            k
            for k in ("bug", "style", "security")
            if f"{k} 지적" in r["expected_traits"]
        ]
        types[found[0] if len(found) == 1 else "?"] += 1
    check(
        "본 세트 positive issue_type bug3/style3/security2",
        types == {"bug": 3, "style": 3, "security": 2, "?": 0},
        str(types),
    )
    secret = sum("secret-scanner" in r["expected_traits"] + r["note"] for r in pos)
    check("본 세트 positive 하드코딩 비밀값 ≤1", secret <= 1, f"{secret}건")

    # ── rule_id 체계 (모든 행) ──
    dep_ids = set(
        re.findall(r"`(DEP-[A-Za-z_]+)`", DEP_MAP.read_text(encoding="utf-8"))
    )
    pep8_ids = set(
        re.findall(r"^## (PEP8-[a-z-]+)", PEP8_DOC.read_text(encoding="utf-8"), re.M)
    )
    text = " ".join(
        r["expected_traits"] + " " + r["forbidden"] + " " + r["note"] for r in rows
    )
    tokens = set(TOKEN_RE.findall(text))
    bad = sorted(
        t
        for t in tokens
        if not (t in dep_ids or t in pep8_ids or RUFF_OK.match(t) or SCAN_OK.match(t))
    )
    print("     사용된 rule_id:", ", ".join(sorted(tokens)))
    check(
        "rule_id 모두 ID 체계에 존재",
        not bad,
        f"없는 ID: {bad}" if bad else f"{len(tokens)}종",
    )

    # ── 도구·입력 형식 (모든 행) ──
    check(
        "expected_tools에 github_* 없음",
        not any("github_" in r["expected_tools"] for r in rows),
    )
    used = {
        x.strip() for r in rows for x in r["expected_tools"].split(";") if x.strip()
    }
    check("expected_tools는 허용 도구만", used <= ALLOWED_TOOLS, str(sorted(used)))
    check(
        "input 형식 (guardrail 예외 제외)",
        all(
            r["category"] == "guardrail" or INPUT_RE.fullmatch(r["input"]) for r in rows
        ),
    )
    paths = [re.search(r"data/eval_diffs/\S+\.diff", r["input"]) for r in rows]
    missing = [
        r["id"]
        for r, m in zip(rows, paths)
        if not m or not (ROOT / m.group(0)).exists()
    ]
    check("diff 파일 존재", not missing, f"없음: {missing}" if missing else "")

    print("\nRESULT:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
