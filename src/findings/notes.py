"""참고(low) 지적 판별: 입력 가드레일이 등급을 낮춘 "참고" 지적인지.

- 인젝션 예시 문장(따옴표·패턴 목록·리뷰 도구를 부르지 않는 명령문)
- 더미로 표시된 비밀값·개인정보(주석 표시, 테스트 경로의 가짜 값 이름)

이 지적들은 Review JSON에는 그대로 남지만, 꼭 고칠 것·요약·화면의 번호 목록·
HITL 번호에서 빠지고 화면 끝 "## 참고"에 모아 보여 줍니다. 가드레일(등급 결정)·
보고서(표시)·리뷰 흐름(요약)이 함께 쓰므로 지적 데이터 모델 도메인에 둡니다.
"""

from __future__ import annotations

from src.findings.schema import Finding


def _scanner_ids(finding: Finding, scanner: str) -> list[str]:
    return [
        (e.rule_id or "")
        for e in finding.evidence
        if (e.rule_id or "").startswith(scanner + ":")
    ]


def is_injection_note(finding: Finding) -> bool:
    """참고(low) 등급의 인젝션 지적인지 (꼭 고칠 것·요약에서 제외할 대상)."""
    return finding.severity == "low" and bool(
        _scanner_ids(finding, "injection-scanner")
    )


def is_dummy_note(finding: Finding) -> bool:
    """더미·가짜 값으로 표시된 비밀값·개인정보 "참고"(low)인지.

    secret-scanner·pii-scanner 지적은 원래 high·medium이라 low면 더미 표시로
    낮춘 것입니다.
    """
    return finding.severity == "low" and bool(
        _scanner_ids(finding, "secret-scanner") or _scanner_ids(finding, "pii-scanner")
    )


def is_note(finding: Finding) -> bool:
    """참고 등급 지적인지 (인젝션 예시 문장·더미 표시 비밀값·개인정보).

    꼭 고칠 것과 요약에서 제외합니다.
    """
    return is_injection_note(finding) or is_dummy_note(finding)


def note_kind(finding: Finding) -> str:
    """화면 "## 참고"에 쓸 종류 이름과 패턴 목록의 기준 (예: "인젝션 예시 문장")."""
    if is_injection_note(finding):
        return "인젝션 예시 문장"
    if _scanner_ids(finding, "pii-scanner"):
        return "가짜 값으로 표시된 개인정보"
    return "더미로 표시된 비밀값"


def note_patterns(finding: Finding) -> list[str]:
    """지적 근거의 스캐너 패턴 이름 (예: ["ignore-instructions"])."""
    return [
        rid.split(":", 1)[1]
        for rid in (e.rule_id or "" for e in finding.evidence)
        if rid.split(":", 1)[0]
        in {"injection-scanner", "secret-scanner", "pii-scanner"}
    ]


# 출력 검증이 코드로 단정하지 못해 설명 앞에 붙이는 표시 (⑨ 이름 미정의 주장 등).
# 이 표시가 붙은 지적은 꼭 고칠 것에서 빼고 "확인 필요"로 따로 셉니다.
UNSURE_PREFIX = "판단 필요"


def is_unsure(finding: Finding) -> bool:
    """출력 검증이 "판단 필요"로 표시한 지적인지 (참고 지적은 제외)."""
    return finding.message.startswith(UNSURE_PREFIX) and not is_note(finding)
