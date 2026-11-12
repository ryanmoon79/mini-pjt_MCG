"""가드레일 검사 결과 형식: 입력 검사(InputCheck)와 출력 근거 검사(OutputCheck)."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.diff import parser as dp
from src.findings.schema import Finding


@dataclass
class InputCheck:
    """입력 가드레일 결과."""

    status: str  # "ok" | "rejected"
    reason: str = ""
    py_files: list[dp.FileDiff] = field(default_factory=list)  # 마스킹 후
    skipped: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)  # security 지적
    masked: dict[str, int] = field(default_factory=dict)  # 패턴명 → 건수
    py_changed_lines: int = 0

    def masked_diff(self) -> str:
        """리뷰어에게 넘길 마스킹된 .py diff 텍스트."""
        return "\n".join(f.text() for f in self.py_files)


# ── 출력 검사: 근거 실재 확인 (SERVICE.md 4번 정책 2) ───────────────────


@dataclass
class OutputCheck:
    """출력 검증 결과."""

    kept: list[Finding] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)  # {"finding", "reason"}
