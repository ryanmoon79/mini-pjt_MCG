"""가드레일: 입력 검사(비밀값·PII 마스킹, 인젝션 탐지, 범위)와 출력 검사.

SERVICE.md 4번 정책을 코드로 구현합니다. 모두 정규식·규칙 기반이며 LLM을 쓰지 않습니다.
- 정책 1: 비밀값·개인정보는 **LLM 호출 전에** 마스킹하고,
  위치마다 security 지적을 만듭니다.
  마스킹 표시 형식은 CLAUDE.md 6번(`[MASKED:<패턴명>]`)을 따릅니다.
- 정책 4: `.py`만 리뷰, `.py`가 없거나 추가·삭제 300줄 초과면 거절.
- 정책 5: 알려진 인젝션 문장은 위치마다 security 지적(`injection-scanner:<패턴명>`).
출력 검사(근거 실재 확인)는 TASKS A5에서 이 파일에 덧붙입니다.
"""

from __future__ import annotations

import re

from src.diff import parser as dp
from src.findings.schema import Evidence, Finding
from src.guardrails.schema import InputCheck
from src.shared import review_settings

# ── 패턴 ──────────────────────────────────────────────────────────────
# 값 부분만 가리려는 패턴은 이름 붙은 그룹 `v`를 씁니다 (없으면 일치 전체를 가림).
SECRET_PATTERNS: dict[str, re.Pattern] = {
    "aws-access-key": re.compile(r"\b(?P<v>(?:AKIA|ASIA)[0-9A-Z]{16})\b"),
    "aws-secret-key": re.compile(
        r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?(?P<v>[A-Za-z0-9/+=]{40})"
    ),
    # 변수 이름에 접두어·접미어가 붙어도 잡도록 \b를 쓰지 않음
    # (예: OPENAI_API_KEY, DB_PASSWORD)
    "api-key": re.compile(
        r"(?i)\w*(?:api[_-]?key|access[_-]?token|secret[_-]?key|token)\w*\s*[=:]\s*"
        r"['\"](?P<v>[A-Za-z0-9_\-./+=]{16,})['\"]"
    ),
    "password": re.compile(
        r"(?i)\w*(?:password|passwd|pwd)\w*\s*[=:]\s*['\"](?P<v>[^'\"\s]{4,})['\"]"
    ),
}


PII_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(
        r"(?P<v>\b[A-Za-z0-9._%+-]+@(?P<d>[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b)"
    ),
    "phone": re.compile(r"(?P<v>\b01[016789]-?\d{3,4}-?\d{4}\b)"),
    "resident-id": re.compile(r"(?P<v>\b\d{6}-[1-4]\d{6}\b)"),
}


# 예시용으로 흔히 쓰는 이메일 도메인은 개인정보로 보지 않습니다 (오탐 줄이기).
EMAIL_ALLOWLIST = {"example.com", "example.org", "example.net", "test.com", "localhost"}


INJECTION_PATTERNS: dict[str, re.Pattern] = {
    "ignore-instructions": re.compile(
        r"(?i)(ignore\s+(all\s+)?(previous|prior|above)\s+instructions"
        r"|이전\s*(의\s*)?지시(사항)?(를|은)?\s*(모두\s*)?(무시|잊))"
    ),
    "claim-no-issues": re.compile(
        r"(?i)(문제\s*(가\s*)?없다고\s*(답|말|보고)|report\s+no\s+(issues|problems)"
        r"|say\s+(there\s+are\s+)?no\s+issues)"
    ),
    "post-without-approval": re.compile(
        r"(?i)((바로|즉시|승인\s*없이)\s*(PR\s*에\s*)?(게시|코멘트)"
        r"|post\s+(this|the)\s+(review|comment)\s+(now|immediately|without))"
    ),
}


SCANNER_TITLES = {
    "secret-scanner": "비밀값 탐지",
    "pii-scanner": "개인정보 탐지",
    "injection-scanner": "프롬프트 인젝션 탐지",
}


def _mask_line(text: str) -> tuple[str, list[tuple[str, str]]]:
    """한 줄의 비밀값·개인정보를 가립니다. (가린 줄, [(스캐너, 패턴명)])."""
    hits: list[tuple[str, str]] = []

    def apply(scanner: str, name: str, pattern: re.Pattern, line: str) -> str:
        def repl(m: re.Match) -> str:
            if name == "email" and m.group("d").lower() in EMAIL_ALLOWLIST:
                return m.group(0)
            hits.append((scanner, name))
            start, end = m.span("v")
            base, seg = m.start(), m.group(0)
            masked = f"[MASKED:{name}]"
            return seg[: start - base] + masked + seg[end - base :]

        return pattern.sub(repl, line)

    for name, pattern in SECRET_PATTERNS.items():
        text = apply("secret-scanner", name, pattern, text)
    for name, pattern in PII_PATTERNS.items():
        text = apply("pii-scanner", name, pattern, text)
    return text, hits


def mask_text(text: str) -> str:
    """임의의 문자열에서 비밀값·개인정보를 가립니다.

    `before_model` 미들웨어와 실행 기록 저장이 같은 함수를 씁니다(이중 방어).
    """
    return "\n".join(_mask_line(line)[0] for line in text.split("\n"))


_SUGGESTIONS = {
    "secret-scanner": "비밀값은 코드에서 지우고 "
    "환경변수나 `.env`(커밋 제외)에서 읽으세요. "
    "이미 커밋된 키는 폐기하고 새로 발급하세요.",
    "pii-scanner": "개인정보는 코드·주석에서 지우고, "
    "필요하면 예시용 값(user@example.com 등)을 쓰세요.",
    "injection-scanner": "리뷰 도구에 지시하는 문장을 코드·주석에서 지우세요. "
    "CiteReview는 이 문장을 따르지 않으며, 게시는 사람 승인 후에만 됩니다.",
}


_MESSAGES = {
    "secret-scanner": "코드에 비밀값({name})이 그대로 들어 있습니다. "
    "저장소에 올라가면 누구나 이 값을 쓸 수 있습니다.",
    "pii-scanner": "코드에 개인정보({name})가 들어 있습니다. "
    "저장소와 외부 서비스로 유출될 수 있습니다.",
    "injection-scanner": "리뷰 도구의 지시를 바꾸려는 문장({name})이 들어 있습니다. "
    "자동 리뷰·게시 도구를 속이려는 프롬프트 인젝션일 수 있습니다.",
}


_SEVERITY = {
    "secret-scanner": "high",
    "pii-scanner": "medium",
    "injection-scanner": "medium",
}


# ── 인젝션 문장 등급 (리뷰 도구를 부르는 명령문만 medium) ─────────────────
# 리뷰 도구·AI를 부르는 말 (이 말이 함께 있어야 "리뷰 도구를 향한 지시"로 봄)
INJECTION_ADDRESSEE = re.compile(
    # AI·LLM은 영문자 경계만 봄 ("AI에게"처럼 한글이 바로 붙어도 잡히게; \b는 한글을
    # 단어 문자로 봐서 놓침)
    r"(?i)(리뷰\s*(도구|봇|어)|코드\s*리뷰어|review(er|\s+(tool|bot))"
    r"|(?<![A-Za-z])AI(?![A-Za-z])|assistant|어시스턴트|(?<![A-Za-z])LLM(?![A-Za-z])"
    r"|언어\s*모델|모델에게|에이전트에게|CiteReview)"
)


_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'|“[^”]*”|‘[^’]*’")


# 정규식 패턴 목록·탐지 설명 맥락 (인젝션을 막는 코드가 예시 문장을 담는 곳)
_PATTERN_CONTEXT = re.compile(
    r"(?i)(re\.compile|정규식|패턴|탐지|차단|막기|막는|regex|pattern|detect)"
)


INJECTION_NOTE_MESSAGE = (
    "참고: 인젝션 예시 문장 포함 ({name}). "
    "따옴표·정규식 패턴·탐지 설명 안의 문장이거나 "
    "리뷰 도구를 부르는 말이 없어, 리뷰 도구를 향한 지시로 판정하지 않았습니다."
)


INJECTION_NOTE_SUGGESTION = (
    "탐지·차단용 예시라면 고칠 필요가 없습니다. 실제로 도구에 내리는 지시라면 지우세요."
)


def injection_grade(text: str, span: tuple[int, int]) -> str:
    """인젝션 문장 한 곳의 등급: "addressed"(security medium) 또는 "example"(참고 low).

    - 맞은 문장이 따옴표 안에 있거나, 그 줄이 정규식·탐지 설명 맥락이면 "example"
    - 리뷰 도구·AI를 부르는 말이 같은 줄에 있는 명령문만 "addressed"
    결정적 규칙이라 처음 보는 형태는 놓칠 수 있습니다 (README 남은 한계).
    승인·게시는 그래프 코드가 강제하므로 등급과 관계없이 무단 게시는 불가합니다.
    """
    start, end = span
    if any(q.start() <= start and end <= q.end() for q in _QUOTED.finditer(text)):
        return "example"
    if _PATTERN_CONTEXT.search(text):
        return "example"
    return "addressed" if INJECTION_ADDRESSEE.search(text) else "example"


def is_injection_note(finding: Finding) -> bool:
    """참고(low) 등급의 인젝션 지적인지 (꼭 고칠 것·요약에서 제외할 대상)."""
    return finding.severity == "low" and any(
        (e.rule_id or "").startswith("injection-scanner:") for e in finding.evidence
    )


def _scanner_finding(scanner: str, name: str, file: str, line: int) -> Finding:
    return Finding(
        issue_type="security",
        file=file,
        line=line,
        severity=_SEVERITY[scanner],
        message=_MESSAGES[scanner].format(name=name),
        evidence=[Evidence(type="tool", source=scanner, rule_id=f"{scanner}:{name}")],
        suggestion=_SUGGESTIONS[scanner],
    )


def check_input(diff_text: str, max_lines: int | None = None) -> InputCheck:
    """diff를 검사하고 마스킹합니다 (그래프 맨 앞 노드가 부름).

    1. 파일을 `.py`와 나머지로 나누고, `.py`가 없거나 변경 한도를 넘으면 거절
       (`max_lines`가 없으면 기본 300줄, 평가는 항상 기본값)
    2. 모든 줄의 비밀값·개인정보를 가리고, 보이는 줄(추가·문맥)마다 security 지적 생성
    3. 알려진 인젝션 문장 위치마다 security 지적 생성 (가리지는 않음)
    """
    files = dp.parse_diff(diff_text)
    py_files, skipped = dp.split_python(files)
    count = dp.py_changed_line_count(files)
    if not py_files:
        return InputCheck(
            status="rejected",
            reason="리뷰할 `.py` 파일이 없습니다. CiteReview는 아직 Python만 지원합니다"
            f" (건너뛴 파일: {', '.join(skipped) or '없음'}).",
            skipped=skipped,
        )
    limit = max_lines or review_settings.MAX_PY_CHANGED_LINES
    if count > limit:
        return InputCheck(
            status="rejected",
            reason=f"`.py` 파일의 추가·삭제 줄 합계가 {count}줄로 "
            f"{limit}줄을 넘습니다. diff를 나눠서 리뷰해 주세요.",
            skipped=skipped,
            py_changed_lines=count,
        )

    result = InputCheck(
        status="ok", py_files=py_files, skipped=skipped, py_changed_lines=count
    )
    for f in py_files:
        for hunk in f.hunks:
            for ln in hunk.lines:
                for name, pattern in INJECTION_PATTERNS.items():
                    match = pattern.search(ln.text)
                    if match and ln.new_no is not None:
                        hit = _scanner_finding(
                            "injection-scanner", name, f.path, ln.new_no
                        )
                        if injection_grade(ln.text, match.span()) == "example":
                            hit = hit.model_copy(
                                update={
                                    "severity": "low",
                                    "message": INJECTION_NOTE_MESSAGE.format(name=name),
                                    "suggestion": INJECTION_NOTE_SUGGESTION,
                                }
                            )
                        result.findings.append(hit)
                ln.text, hits = _mask_line(ln.text)
                for scanner, name in hits:
                    result.masked[name] = result.masked.get(name, 0) + 1
                    if ln.new_no is not None:
                        hit = _scanner_finding(scanner, name, f.path, ln.new_no)
                        # 더미로 표시된 비밀값은 low "참고" (지우지 않음, 마스킹 그대로)
                        if scanner == "secret-scanner" and _marked_dummy(hunk, ln):
                            hit = hit.model_copy(
                                update={
                                    "severity": "low",
                                    "message": DUMMY_SECRET_MESSAGE.format(name=name),
                                    "suggestion": DUMMY_SECRET_SUGGESTION,
                                }
                            )
                        result.findings.append(hit)
    return result


# ── 더미로 표시된 비밀값 (인젝션 D2와 같은 방식의 등급 조정) ─────────────────

# 주석 안의 더미 표시. 값(따옴표 안 문자열)은 보지 않음: AWS 문서 예시 키처럼
# 값에 EXAMPLE이 들어 있어도 진짜 키와 구분할 수 없기 때문 (평가셋 18번은 high 유지).
DUMMY_MARK_RE = re.compile(
    r"더미|가짜|예시|샘플|테스트|dummy|example|test|fake|sample", re.IGNORECASE
)


DUMMY_SECRET_MESSAGE = (
    "참고: 더미로 표시된 비밀값({name})입니다. 주석에 더미·예시 표시가 있어 꼭 고칠 "
    "것에서 뺐지만, 도구는 진짜 키인지 확인하지 못합니다."
)


DUMMY_SECRET_SUGGESTION = (
    "진짜 키라면 바로 폐기·교체하고 환경변수로 옮기세요. 더미라도 키 형식의 값은 "
    "환경변수나 테스트 픽스처로 분리하는 편이 안전합니다."
)


def _comment_part(text: str) -> str:
    """줄에서 문자열 밖의 `#` 주석 부분만 (따옴표 안의 `#`·값은 제외)."""
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return text[i + 1 :]
        i += 1
    return ""


def _marked_dummy(hunk, line) -> bool:
    """비밀값이 탐지된 줄이 더미로 표시됐는지.

    - 그 줄의 `#` 주석 부분(문자열 밖)에 더미 표시가 있거나
    - 바로 위 1~2줄이 **주석 줄**(`#`로 시작)이고 거기에 더미 표시가 있으면 True.
      위로 올라가다 주석 줄이 아니면(코드·빈 줄) 멈춥니다. 삭제된 줄(-)은 건너뜁니다.
    """
    if DUMMY_MARK_RE.search(_comment_part(line.text)):
        return True
    visible = [x for x in hunk.lines if x.kind != "-"]
    idx = next((i for i, x in enumerate(visible) if x is line), None)
    if idx is None:
        return False
    for prev in reversed(visible[max(0, idx - 2) : idx]):
        stripped = prev.text.strip()
        if not stripped.startswith("#"):
            break
        if DUMMY_MARK_RE.search(stripped):
            return True
    return False


def is_dummy_secret_note(finding: Finding) -> bool:
    """더미로 표시된 비밀값 "참고"(low)인지 (secret-scanner는 원래 high)."""
    return finding.severity == "low" and any(
        (e.rule_id or "").startswith("secret-scanner:") for e in finding.evidence
    )


def is_note(finding: Finding) -> bool:
    """참고 등급 지적인지 (인젝션 예시 문장·더미 표시 비밀값).

    꼭 고칠 것과 요약에서 제외합니다.
    """
    return is_injection_note(finding) or is_dummy_secret_note(finding)
