"""줄 길이(E501) 지적: 리뷰어 대신 ruff 결과로 코드가 직접 만듭니다(숫자는 ruff 값)."""

from __future__ import annotations

import re

from src.findings.schema import Evidence, Finding

_LINE_TOO_LONG_RE = re.compile(r"\((\d+) > (\d+)\)")


LINE_SPLIT_HINT = (
    "괄호 안에서 줄을 바꾸거나 긴 문자열·주석·docstring을 여러 줄로 나눕니다."
)


def line_length_findings(ruff_rows: list[dict], settings, doc: dict | None) -> list:
    """ruff E501 결과로 줄 길이 지적을 코드가 직접 만듭니다 (리뷰어는 지적하지 않음).

    길이 숫자는 ruff가 잰 값이고, 근거는 ruff 출력(tool)과 PEP 8 최대 줄 길이(doc).
    """
    out = []
    for r in ruff_rows:
        if r["code"] != "E501":
            continue
        m = _LINE_TOO_LONG_RE.search(r["message"])
        size, limit = (m.group(1), m.group(2)) if m else ("?", settings.line_length)
        evidence = [Evidence(type="tool", source="ruff", rule_id="ruff:E501")]
        if doc:
            evidence.append(
                Evidence(type="doc", source=doc["source"], rule_id=doc["rule_id"])
            )
        out.append(
            Finding(
                issue_type="style",
                file=r["file"],
                line=r["line"],
                severity="low",
                message=(
                    f"줄 길이가 {size}칸으로 기준 {limit}칸({settings.ruff_source})을 "
                    "넘습니다. 긴 줄은 읽기와 diff 비교를 어렵게 합니다."
                ),
                evidence=evidence,
                suggestion=LINE_SPLIT_HINT,
            )
        )
    return out
