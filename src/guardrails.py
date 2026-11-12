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
from dataclasses import dataclass, field

from src import config
from src import diff_parser as dp
from src.schema import Evidence, Finding

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


def check_input(diff_text: str) -> InputCheck:
    """diff를 검사하고 마스킹합니다 (그래프 맨 앞 노드가 부름).

    1. 파일을 `.py`와 나머지로 나누고, `.py`가 없거나 300줄을 넘으면 거절
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
    if count > config.MAX_PY_CHANGED_LINES:
        return InputCheck(
            status="rejected",
            reason=f"`.py` 파일의 추가·삭제 줄 합계가 {count}줄로 "
            f"{config.MAX_PY_CHANGED_LINES}줄을 넘습니다. diff를 나눠서 리뷰해 주세요.",
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
                    if pattern.search(ln.text) and ln.new_no is not None:
                        hit = _scanner_finding(
                            "injection-scanner", name, f.path, ln.new_no
                        )
                        result.findings.append(hit)
                ln.text, hits = _mask_line(ln.text)
                for scanner, name in hits:
                    result.masked[name] = result.masked.get(name, 0) + 1
                    if ln.new_no is not None:
                        result.findings.append(
                            _scanner_finding(scanner, name, f.path, ln.new_no)
                        )
    return result


# ── 출력 검사: 근거 실재 확인 (SERVICE.md 4번 정책 2) ───────────────────


@dataclass
class OutputCheck:
    """출력 검증 결과."""

    kept: list[Finding] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)  # {"finding", "reason"}


def _verify_evidence(
    ev: Evidence,
    finding: Finding,
    visible: dict[str, dict],
    ruff_ids: set[str],
    retrieved: set[str],
    scanner_ids: set[str],
) -> str | None:
    """근거 하나가 이번 리뷰에 실제로 있었는지 확인합니다.

    문제가 없으면 None, 있으면 사유 문자열을 돌려줍니다.
    """
    if ev.type == "code":
        path, _, num = ev.source.rpartition(":")
        path = path or finding.file
        line = int(num) if num.isdigit() else finding.line
        if line not in visible.get(path, {}):
            return f"code 근거 {ev.source}가 diff에 없음"
        return None
    if ev.type == "doc":
        if ev.rule_id not in retrieved:
            return f"doc 근거 {ev.rule_id}가 이번 검색 결과에 없음"
        return None
    # tool
    rid = ev.rule_id or ""
    if rid.startswith("ruff:"):
        return None if rid in ruff_ids else f"tool 근거 {rid}가 이번 ruff 출력에 없음"
    if rid in scanner_ids:
        return None
    return f"tool 근거 {rid or ev.source}가 이번 도구 출력에 없음"


def check_output(
    findings: list[Finding],
    check: InputCheck,
    ruff_findings: list[dict],
    retrieved: set[str],
) -> OutputCheck:
    """지적마다 근거가 실제로 존재하는지 기계적으로 확인합니다 (LLM 없음).

    - 지적의 파일·줄이 diff에 보이는 줄이 아니면 지적을 제거합니다.
    - 확인되지 않는 근거는 지우고, 확인된 근거가 하나도 없으면 지적을 제거합니다.
    - 근거가 지적을 *뒷받침하는지*는 보지 않습니다 (RAGAS faithfulness로 보고).
    """
    visible = {f.path: dp.new_side_lines(f) for f in check.py_files}
    ruff_ids = {r["rule_id"] for r in ruff_findings}
    scanner_ids = {e.rule_id for f in check.findings for e in f.evidence}
    result = OutputCheck()
    for finding in findings:
        if finding.line not in visible.get(finding.file, {}):
            result.dropped.append(
                {
                    "finding": finding,
                    "reason": f"{finding.file}:{finding.line}가 diff에 없음",
                }
            )
            continue
        good, reasons = [], []
        for ev in finding.evidence:
            problem = _verify_evidence(
                ev, finding, visible, ruff_ids, retrieved, scanner_ids
            )
            (reasons.append(problem) if problem else good.append(ev))
        if good:
            result.kept.append(finding.model_copy(update={"evidence": good}))
        else:
            result.dropped.append({"finding": finding, "reason": "; ".join(reasons)})
    return result


# ── 출력 검사: 금지된 대체 코드 (deprecated API를 다시 권하는 제안) ──────

DEPRECATED_MAP_PATH = config.CORPUS_DIR / "deprecated_map.md"
FALLBACK_SUGGESTION = "대체 방법은 근거 문서 참고"
_CODE_IN_SUGGESTION_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)
_IMPORT_LINE_RE = re.compile(r"^\s*(from\s+\S+\s+import\s+.+|import\s+\S+.*)$", re.M)


def removed_import_patterns(path=DEPRECATED_MAP_PATH) -> dict[str, re.Pattern]:
    """deprecated_map.md에서 1.x 상태가 "제거"인 행의 import를 정규식으로 만듭니다.

    예: `from langchain.chains import LLMChain` → `from langchain.chains import` 패턴.
    `langchain_classic` 사용은 행의 상태와 관계없이 금지합니다(호환용 패키지).
    """
    patterns = {"DEP-langchain_classic": re.compile(r"\blangchain_classic\b")}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `DEP-"):
            continue
        cells = [c.strip() for c in line.replace("\\|", "\x00").strip("|").split("|")]
        rule_id, old_use, status = cells[0].strip("`"), cells[1], cells[2]
        m = re.search(r"from\s+([\w.]+)\s+import\s+([\w.]+)", old_use)
        if "제거" not in status or not m:
            continue
        module, name = re.escape(m.group(1)), m.group(2)
        if name.strip(".") and "등" not in old_use:
            # 이름이 정해진 행: 그 이름을 import할 때만 (예: create_agent는 허용)
            patterns[rule_id] = re.compile(
                rf"\bfrom\s+{module}\s+import\s+[^\n]*\b{re.escape(name)}\b"
            )
        else:
            # "..."·"등": 모듈 전체가 제거됨
            patterns[rule_id] = re.compile(rf"\bfrom\s+{module}\s+import\b")
    return patterns


def forbidden_suggestion(
    suggestion: str, patterns: dict[str, re.Pattern]
) -> str | None:
    """고치는 방법의 **코드**(코드 블록·import 문)에서 금지된 API를 찾습니다.

    찾으면 그 rule_id, 없으면 None을 돌려줍니다.
    설명 문장 속 언급("langchain_classic으로 옮겨졌지만…")은 검사하지 않습니다.
    """
    code = "\n".join(_CODE_IN_SUGGESTION_RE.findall(suggestion))
    code += "\n" + "\n".join(_IMPORT_LINE_RE.findall(suggestion))
    for rule_id, pattern in patterns.items():
        if pattern.search(code):
            return rule_id
    return None


# ── 미들웨어: 모든 리뷰어 모델 호출 직전에 한 번 더 마스킹 (이중 방어) ─────


def _mask_message(message):
    content = message.content
    if isinstance(content, str):
        return message.model_copy(update={"content": mask_text(content)})
    if isinstance(content, list):
        parts = [
            {**p, "text": mask_text(p["text"])}
            if isinstance(p, dict) and isinstance(p.get("text"), str)
            else p
            for p in content
        ]
        return message.model_copy(update={"content": parts})
    return message


def masking_middleware():
    """`create_agent`에 붙이는 마스킹 미들웨어 (모델에 가는 메시지를 가림).

    입력 가드레일 노드가 이미 diff를 가렸지만, 도구 출력·이전 메시지 등 모델로 가는
    모든 내용을 모델 호출 직전에 같은 `mask_text`로 한 번 더 가립니다.
    """
    from langchain.agents.middleware import AgentMiddleware

    class MaskingMiddleware(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            masked = [_mask_message(m) for m in request.messages]
            return handler(request.override(messages=masked))

    return MaskingMiddleware()


# ── 출력 검사: 설명 문장 속 langchain_classic 권장 (1차 평가 2번 케이스) ────

CLASSIC_RE = re.compile(r"langchain[_-]classic", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|\n+")


def mentions_classic(text: str) -> bool:
    """문장 어디에든 `langchain_classic`이 나오면 True (설명·요약·추천 공통)."""
    return bool(CLASSIC_RE.search(text or ""))


def strip_classic_sentences(text: str) -> str:
    """`langchain_classic`이 나오는 문장을 지운 나머지를 돌려줍니다 (결정적 대체)."""
    kept = [
        s for s in _SENTENCE_SPLIT_RE.split(text or "") if s and not mentions_classic(s)
    ]
    return " ".join(s.strip() for s in kept).strip()


def strip_classic_sentences_keep_code(text: str) -> str:
    """코드 블록은 그대로 두고, 블록 밖 설명에서 langchain_classic 문장만 지웁니다.

    코드 블록 안에 langchain_classic이 있으면 빈 문자열(대체 문구를 쓰라는 뜻).
    """
    parts = re.split(r"(```(?:python)?\n.*?```)", text or "", flags=re.DOTALL)
    out = []
    for part in parts:
        if part.startswith("```"):
            if mentions_classic(part):
                return ""
            out.append(part)
        elif part.strip():
            cleaned = strip_classic_sentences(part)
            if cleaned:
                out.append(cleaned)
    return "\n".join(out).strip()


# ── PEP 8 근거 ↔ ruff 교차 확인 (근거-지적 일치율, 선택 작업 C2 공용) ─────────


def _pep8_to_ruff() -> dict[str, tuple[str, ...]]:
    """PEP 8 섹션 → 대응 ruff 코드 (evaluation/README.md 1절 연결표를 뒤집은 것).

    `N8xx`(이름 규칙)는 코드 접두어 "N8"로 맞춥니다.
    """
    from src.tools import RUFF_TO_PEP8

    table: dict[str, list[str]] = {}
    for code, section in RUFF_TO_PEP8.items():
        table.setdefault(section, []).append(code)
    table.setdefault("PEP8-naming-conventions", []).append("N8")
    return {k: tuple(v) for k, v in table.items()}


def ruff_truth(py_files: list[dp.FileDiff]) -> set[tuple[str, int, str]]:
    """diff로 다시 돌린 ruff 위반 (파일, 변경 후 줄, 코드) 집합.

    리뷰어가 `ruff_check`를 불렀는지와 관계없이 같은 기준을 쓰려고 직접 계산합니다.
    """
    from src.tools import run_ruff

    return {(r["file"], r["line"], r["code"]) for f in py_files for r in run_ruff(f)}


def pep8_alignment(
    file: str,
    line: int,
    issue_type: str,
    rule_ids: list[str],
    truth: set[tuple[str, int, str]],
) -> str | None:
    """`PEP8-*` 근거를 단 style 지적이 그 줄의 ruff 위반과 맞는지 판정합니다.

    돌려주는 값: "match"(대응 위반 있음) /
    "mismatch"(연결표에 있는 섹션인데 위반 없음) /
    "undecidable"(연결표에 없는 섹션만 인용) / None(PEP8 근거 style 지적이 아님).
    인용한 섹션 중 하나라도 대응 위반이 있으면 "match"입니다.
    """
    sections = [r for r in rule_ids if r and r.startswith("PEP8-")]
    if issue_type != "style" or not sections:
        return None
    table = _pep8_to_ruff()
    known = [s for s in sections if s in table]
    if not known:
        return "undecidable"
    codes_here = {c for f, n, c in truth if f == file and n == line}
    for s in known:
        if any(c.startswith(p) for c in codes_here for p in table[s]):
            return "match"
    return "mismatch"


def cross_check_pep8(
    findings: list[Finding], truth: set[tuple[str, int, str]]
) -> tuple[list[Finding], list[dict]]:
    """선택 작업 C2: PEP 8 근거가 그 줄의 ruff 위반과 맞지 않는 style 지적을 거릅니다.

    - "mismatch"인 지적에서 맞지 않는 `PEP8-*` 근거를 지웁니다.
      남은 근거 중에 다른 문서 근거(`DEP-*` 등)나 도구 근거가 있으면 지적은 유지하고,
      코드 위치 근거만 남으면(= PEP 8 주장을 뒷받침할 것이 없음) 지적을 제거합니다.
    - 연결표에 없는 섹션만 인용한 지적("undecidable")은 건드리지 않습니다.
    돌려주는 값: (남은 지적, 제거한 지적 [{file, line, rule_ids, reason}]).
    """
    kept, removed = [], []
    table = _pep8_to_ruff()
    for f in findings:
        ids = [e.rule_id or "" for e in f.evidence]
        verdict = pep8_alignment(f.file, f.line, f.issue_type, ids, truth)
        if verdict != "mismatch":
            kept.append(f)
            continue
        rest = [e for e in f.evidence if (e.rule_id or "") not in table]
        if any(e.type in ("doc", "tool") for e in rest):
            kept.append(f.model_copy(update={"evidence": rest}))
            continue
        removed.append(
            {
                "file": f.file,
                "line": f.line,
                "rule_ids": [i for i in ids if i],
                "reason": "PEP 8 근거에 대응하는 ruff 위반이 그 줄에 없음",
            }
        )
    return kept, removed
