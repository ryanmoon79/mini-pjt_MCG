"""출력 검증: 근거 실재 확인, PEP 8 ↔ ruff 교차 확인(C2), 금지된 대체 코드·classic 권장,
파싱 실패 주장 검증(④), API 존재 주장 검증(⑥).

모두 결정적 검사이며 LLM을 쓰지 않습니다(SERVICE.md 4번 정책 2).
"""

from __future__ import annotations

import ast
import re

from src.diff import parser as dp
from src.findings.schema import Evidence, Finding
from src.guardrails.schema import InputCheck, OutputCheck
from src.shared import config


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
    from src.toolkit.ruff import RUFF_TO_PEP8

    table: dict[str, list[str]] = {}
    for code, section in RUFF_TO_PEP8.items():
        table.setdefault(section, []).append(code)
    table.setdefault("PEP8-naming-conventions", []).append("N8")
    return {k: tuple(v) for k, v in table.items()}


def ruff_truth(py_files: list[dp.FileDiff]) -> set[tuple[str, int, str]]:
    """diff로 다시 돌린 ruff 위반 (파일, 변경 후 줄, 코드) 집합.

    리뷰어가 `ruff_check`를 불렀는지와 관계없이 같은 기준을 쓰려고 직접 계산합니다.
    """
    from src.toolkit.ruff import run_ruff

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


# ── 출력 검사: 파싱 실패를 주장하는 지적 (evaluation/notes/case10) ──

# 파싱 실패(코드가 아예 실행되지 않음)를 주장하는 표현만. "들여쓰기 때문에 return이
# 루프 안에 있다" 같은 논리 버그 설명은 여기에 걸리지 않습니다.
PARSE_CLAIM_RE = re.compile(
    r"SyntaxError|IndentationError|TabError|문법\s*오류|구문\s*오류", re.IGNORECASE
)


def claims_parse_failure(finding: Finding) -> bool:
    """지적 설명(message)이 파싱 실패(SyntaxError 등)를 주장하는지."""
    return bool(PARSE_CLAIM_RE.search(finding.message))


def verify_syntax_claims(
    findings: list[Finding], py_files: list[dp.FileDiff]
) -> tuple[list[Finding], list[dict], list[dict]]:
    """파싱 실패를 주장하는 지적을 변경 후 파일의 `ast.parse`로 검증합니다 (LLM 없음).

    - 변경 후 파일 전체가 diff에 보이면 다시 만들어 파싱하고, 파싱되면 그 지적은
      사실이 아니므로 제거합니다.
    - 파일 일부만 보이면(빠진 줄을 빈 줄로 채우면 들여쓰기가 깨져 가짜 오류가 남)
      검증할 수 없으므로 지적을 유지하고 "검증 불가"로 기록합니다.
    - 파싱 실패를 주장하지 않는 지적(논리 버그 등)은 건드리지 않습니다.

    Returns:
        (유지할 지적, 제거 기록, 검증 불가 기록). 제거 기록에는 평가의 반사실
        판정(이 지적이 남았다면 실패했을까)을 위해 지적 전체(마스킹 후)를 남깁니다.
    """
    import ast

    from src.toolkit.ruff import reconstruct_file

    by_path = {f.path: f for f in py_files}
    kept, dropped, unverified = [], [], []
    for f in findings:
        target = by_path.get(f.file)
        if not claims_parse_failure(f) or target is None:
            kept.append(f)
            continue
        source, complete = reconstruct_file(target)
        where = {"file": f.file, "line": f.line, "issue_type": f.issue_type}
        if not complete:
            kept.append(f)
            unverified.append({**where, "reason": "파일 일부만 보여 파싱 검증 불가"})
            continue
        try:
            ast.parse(source)
        except SyntaxError as exc:  # 실제로 파싱 실패 → 지적 유지
            kept.append(f)
            unverified.append({**where, "reason": f"실제 파싱 실패: {exc.msg}"})
            continue
        dropped.append(
            {
                **where,
                "severity": f.severity,
                "reason": "변경 후 파일이 ast.parse로 파싱됨 (주장이 사실 아님)",
                "finding": f.model_dump(),
            }
        )
    return kept, dropped, unverified


# ── 출력 검사: API 존재 주장 검증 (round4, Sonnet의 1.x API 착각 대응) ─────

# "X가 1.x에 없다/존재하지 않는다/제거됐다/import되지 않는다"는 주장 표현
ABSENCE_CLAIM_RE = re.compile(
    r"존재하지 않|없는\s*(경로|API|모듈|함수|클래스|이름|심볼)|"
    r"(에|에는|에서)\s*없(습니다|다|으며|어|는)|제거(되었|됐|된|돼)|삭제(되었|됐|된)|"
    r"import(할 수 없|되지 않|가 안|이 안)|ImportError|ModuleNotFoundError",
)


# 검증 대상 패키지 (리뷰 범위인 LangChain·LangGraph만; 다른 이름은 import하지 않음)
API_CHECK_PREFIXES = ("langchain", "langgraph")


# 이름 목록은 같은 줄 안에서만 (줄바꿈을 넘으면 다음 import 줄을 삼킴)
_FROM_IMPORT_RE = re.compile(
    r"from[ \t]+([\w.]+)[ \t]+import[ \t]+\(?[ \t]*([\w \t,]+)"
)


_DOTTED_RE = re.compile(r"`((?:langchain|langgraph)[\w]*(?:\.[\w]+)+)`")


def _api_targets(text: str) -> list[tuple[str, str | None]]:
    """글·코드에서 (모듈, 이름) 후보를 뽑습니다. `import X`·`X.Y` 표기도 받습니다."""
    out: list[tuple[str, str | None]] = []
    for module, names in _FROM_IMPORT_RE.findall(text):
        for name in re.split(r"[\s,]+", names.strip()):
            if name and name != "as":
                out.append((module, name))
    for dotted in _DOTTED_RE.findall(text):
        module, _, name = dotted.rpartition(".")
        out.append((module, name))
    for module in re.findall(r"^\s*import\s+([\w.]+)", text, re.M):
        out.append((module, None))
    return [
        (m, n)
        for m, n in dict.fromkeys(out)
        if m.split(".")[0].startswith(API_CHECK_PREFIXES)
    ]


def _importable(module: str, name: str | None) -> bool:
    """설치된 패키지에서 실제로 import되는지 (모듈 import + 이름 존재)."""
    import importlib

    try:
        mod = importlib.import_module(module)
    except Exception:
        return False
    if name is None or hasattr(mod, name):
        return True
    try:  # 하위 모듈 이름일 수도 있음 (from pkg import submodule)
        importlib.import_module(f"{module}.{name}")
        return True
    except Exception:
        return False


_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\(([^()]*)\)")


def _file_imports(file: dp.FileDiff | None) -> dict[str, str]:
    """변경 후 파일에 보이는 `from X import Y`로 이름 → 모듈 (LangChain·LangGraph만)."""
    text = "\n".join(dp.new_side_lines(file).values()) if file else ""
    return {
        n: m
        for m, n in _api_targets(text)
        if n is not None  # `import X`는 호출 검증에 쓰지 않음
    }


def _call_ok(module: str, name: str, kwargs: list[str]) -> bool:
    """함수가 import되고, 주장에 적힌 키워드 인자가 설치본 시그니처에 모두 있는지."""
    import importlib
    import inspect

    try:
        fn = getattr(importlib.import_module(module), name)
        params = inspect.signature(fn).parameters
    except Exception:
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return all(k in params for k in kwargs)


def _call_targets(message: str, imports: dict[str, str]) -> list[tuple[str, str, list]]:
    """설명에 적힌 호출 `f(a=…)` 중 이 파일이 import한 함수: (모듈, 이름, 키워드)."""
    out = []
    for name, args in _CALL_RE.findall(message):
        if name in imports:
            kwargs = re.findall(r"\b([A-Za-z_]\w*)\s*=", args)
            out.append((imports[name], name, kwargs))
    return out


def _package_version(module: str) -> str:
    from importlib.metadata import PackageNotFoundError, version

    top = module.split(".")[0]
    try:
        return f"{top.replace('_', '-')} {version(top.replace('_', '-'))}"
    except PackageNotFoundError:
        return top


def verify_api_claims(
    findings: list[Finding], py_files: list[dp.FileDiff]
) -> tuple[list[Finding], list[dict]]:
    """API가 없다·제거됐다는 지적을 설치된 패키지의 실제 import로 검증합니다 (결정적).

    - 대상: 설명(message)이 부재를 주장하는 bug·security 지적. 그 지적의 문장과 지적한
      줄의 코드에서 LangChain·LangGraph import 대상(모듈, 이름)을 뽑고, 문장에 이름이나
      모듈이 언급된 것만 봅니다.
    - deprecated_map.md에서 1.x 상태가 "제거"인 API면 import 결과와 관계없이 유지합니다.
    - 뽑은 대상이 모두 실제로 import되면 주장이 사실이 아니므로 제거하고 사유를
      남깁니다. 대상을 못 뽑았거나 하나라도 import가 안 되면 유지합니다.

    Returns:
        (유지할 지적, 제거 기록 — 대상·패키지 버전·지적 전체)
    """
    removed = removed_import_patterns()
    by_path = {f.path: f for f in py_files}
    kept, dropped = [], []
    for f in findings:
        if f.issue_type not in {"bug", "security"} or not ABSENCE_CLAIM_RE.search(
            f.message
        ):
            kept.append(f)
            continue
        file = by_path.get(f.file)
        code = dp.new_side_lines(file).get(f.line, "") if file else ""
        targets = [
            (m, n)
            for m, n in _api_targets(f.message + "\n" + code)
            if (n and n in f.message) or m in f.message
        ]
        # 호출 주장: "create_agent(model=…, system_prompt=…)는 없는 API"처럼 이 파일이
        # import한 함수의 호출을 문제 삼으면, 설치본 시그니처로 키워드 인자까지 확인
        calls = _call_targets(f.message, _file_imports(file))
        statements = [
            f"from {m} import {n}" if n else f"import {m}" for m, n in targets
        ] + [f"{n}({', '.join(k + '=' for k in kw)}) — {m}" for m, n, kw in calls]
        on_removed_list = any(
            p.search(s) for s in statements + [code] for p in removed.values()
        )
        ok = all(_importable(m, n) for m, n in targets) and all(
            _call_ok(m, n, kw) for m, n, kw in calls
        )
        if not (targets or calls) or on_removed_list or not ok:
            kept.append(f)
            continue
        targets = targets + [(m, n) for m, n, _ in calls]
        dropped.append(
            {
                "file": f.file,
                "line": f.line,
                "issue_type": f.issue_type,
                "severity": f.severity,
                "targets": statements,
                "reason": "설치된 패키지에서 import됨 ("
                + ", ".join(sorted({_package_version(m) for m, _ in targets}))
                + ") — 없다·제거됐다는 주장이 사실이 아님",
                "finding": f.model_dump(),
            }
        )
    return kept, dropped


# ── 출력 검사: 자기부정 지적 (round3 부록 2 — 본문이 스스로 버그가 없다고 결론) ──

# 지적 본문이 "결론으로" 버그가 없다·실제로는 동작한다고 말하는 표현.
# 양보절("문제가 없지만 None이면 실패")은 걸리지 않도록 문장 끝(마침표·줄 끝)의
# 결론만 봅니다.
SELF_NEGATION_PATTERNS = {
    "실제 버그 없음": re.compile(
        r"실제(로는|로)?\s*(버그|문제)(가|는)?\s*(없(음|습니다|다)|아닙니다)"
    ),
    "실제로는 동작": re.compile(r"실제로는\s*(정상\s*)?(동작|작동)(합니다|한다|함)"),
    "문제없음 결론": re.compile(
        r"(문제|버그)\s*(가|는)?\s*없(습니다|음|다|으므로 괜찮)(?=\s*[.。!]|\s*$)"
    ),
    "버그 아님": re.compile(r"버그(가|는)?\s*아닙니다|버그가?\s*아님(?=\s*[.。]|\s*$)"),
    "괜찮다는 결론": re.compile(r"(이므로|므로|때문에)\s*괜찮습니다"),
    # 제출 전 수정 (ac91538 자기 리뷰 tests 87행 오탐): 실패 경로가 없다고 스스로 결론
    "실패 경로 없음": re.compile(
        r"(실행\s*)?실패\s*(하는\s*)?경로(가|는)?\s*없(습니다|음|다)(?!지만)"
    ),
    "지적 철회": re.compile(
        r"이\s*지적은\s*(제거|철회|취소|무시)(해야|합니다|하세요|됩니다|함|\b)"
    ),
}


def self_negation(message: str) -> str | None:
    """지적 본문의 자기부정 결론 패턴 이름 (없으면 None)."""
    for name, pattern in SELF_NEGATION_PATTERNS.items():
        if pattern.search(message):
            return name
    return None


def drop_self_negated(findings: list[Finding]) -> tuple[list[Finding], list[dict]]:
    """bug·security 지적 중 본문이 스스로 "실제 버그 없음"이라고 결론 낸 것을 제거.

    결정적 검사입니다(LLM 없음).

    리뷰어가 추론 과정을 그대로 적다가 "재검토: … 실제 버그 없음"으로 끝낸 지적
    (round3 Sonnet: day07 113행, day05 567행)을 걸러 냅니다. 입력 가드레일이 만든
    security 지적에는 쓰지 않습니다(호출하는 쪽이 리뷰어 지적만 넘김).

    Returns:
        (유지할 지적, 제거 기록 — 걸린 패턴 이름과 지적 전체)
    """
    kept, dropped = [], []
    for f in findings:
        hit = self_negation(f.message) if f.issue_type in {"bug", "security"} else None
        if hit is None:
            kept.append(f)
            continue
        dropped.append(
            {
                "file": f.file,
                "line": f.line,
                "issue_type": f.issue_type,
                "severity": f.severity,
                "pattern": hit,
                "reason": f"지적 본문이 스스로 문제없다고 결론 냄 ({hit})",
                "finding": f.model_dump(),
            }
        )
    return kept, dropped


# ── 출력 검사: 이름 미정의 주장 검증 (round4 이후, f8e0c93 재리뷰의 NameError 오탐) ──

# "정의되지 않은 이름"·NameError·UnboundLocalError를 주장하는 표현
NAME_CLAIM_RE = re.compile(
    r"NameError|UnboundLocalError|정의되지\s*않|미정의|is not defined|undefined name"
    # 제출 전 수정 (round5 day07 113행 오탐): "초기화되지 않은 상태" 계열
    r"|초기화되지\s*않|초기화\s*(전|되기\s*전)에",
    re.IGNORECASE,
)
_BACKTICK_NAME_RE = re.compile(r"`([A-Za-z_]\w*)`")
_QUOTED_NAME_RE = re.compile(r"name '([A-Za-z_]\w*)'")

NAME_UNSURE_PREFIX = "판단 필요(이름 정의 여부를 코드로 확인하지 못함): "


def claims_undefined_name(finding: Finding) -> bool:
    """지적 설명(message)이 이름 미정의(NameError 등)를 주장하는지."""
    return bool(NAME_CLAIM_RE.search(finding.message))


def _target_names(node) -> set[str]:
    """대입 대상(Name·Tuple·List·Starred)에서 이름을 모읍니다."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for e in node.elts:
            out |= _target_names(e)
        return out
    return set()


def _walrus_names(node) -> set[str]:
    """식 안의 `:=` 대입 이름 (중첩 함수·람다 안은 제외)."""
    out: set[str] = set()
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.NamedExpr):
            out |= _target_names(n.target)
        if isinstance(n, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return out


def _import_names(stmt) -> set[str]:
    """import 문이 묶는 이름 (`import *`는 제외)."""
    return {(a.asname or a.name.split(".")[0]) for a in stmt.names if a.name != "*"}


def _with_names(stmt) -> set[str]:
    """with 문의 `as` 대상 이름."""
    out: set[str] = set()
    for i in stmt.items:
        if i.optional_vars is not None:
            out |= _target_names(i.optional_vars)
    return out


def _binds(stmt, name: str) -> bool:
    """문장 하나가 실행되면 `name`이 **반드시** 묶이는지 (분기 한쪽만이면 False)."""

    def any_binds(body: list) -> bool:
        return any(_binds(s, name) for s in body)

    if isinstance(stmt, ast.Assign):
        return any(name in _target_names(t) for t in stmt.targets) or (
            name in _walrus_names(stmt.value)
        )
    if isinstance(stmt, ast.AnnAssign):
        return stmt.value is not None and name in _target_names(stmt.target)
    if isinstance(stmt, (ast.Import, ast.ImportFrom)):
        return name in _import_names(stmt)
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return stmt.name == name
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return name in _with_names(stmt) or any_binds(stmt.body)
    if isinstance(stmt, ast.If):
        return name in _walrus_names(stmt.test) or (
            bool(stmt.orelse) and any_binds(stmt.body) and any_binds(stmt.orelse)
        )
    if isinstance(stmt, ast.Try):
        if any_binds(stmt.finalbody):
            return True
        # 예외가 없으면 body 다음 else가 실행되므로 둘 중 하나가 묶고, 모든 except가
        # 묶어야 함 (day07 113행: try/except/else 안 모든 경로에서 status·detail 정의)
        main_path = any_binds(stmt.body) or any_binds(stmt.orelse)
        return main_path and all(any_binds(h.body) for h in stmt.handlers)
    if isinstance(stmt, ast.Expr):
        return name in _walrus_names(stmt.value)
    return False


def _header_binds(parent, field: str, name: str) -> bool:
    """감싸는 문장의 머리(for 대상·with as·except as·조건의 :=)가 `name`을 묶는지."""
    if isinstance(parent, (ast.For, ast.AsyncFor)) and field == "body":
        return name in _target_names(parent.target)
    if isinstance(parent, (ast.With, ast.AsyncWith)) and field == "body":
        return name in _with_names(parent)
    if isinstance(parent, ast.ExceptHandler):
        return parent.name == name
    if isinstance(parent, ast.Try) and field == "orelse":
        # else는 try 본문이 예외 없이 끝났을 때만 실행됨
        return any(_binds(s, name) for s in parent.body)
    if isinstance(parent, (ast.If, ast.While)) and field != "test":
        return name in _walrus_names(parent.test)
    return False


def _parents(tree) -> dict:
    """노드 → (부모, 필드 이름, 목록 안 위치 또는 None) 대응표."""
    out = {}
    for parent in ast.walk(tree):
        for field, value in ast.iter_fields(parent):
            if isinstance(value, list):
                for i, child in enumerate(value):
                    if isinstance(child, ast.AST):
                        out[child] = (parent, field, i)
            elif isinstance(value, ast.AST):
                out[value] = (parent, field, None)
    return out


def _dominated(use, name: str, parents: dict, stop) -> bool:
    """`use`에 오기 전에 `name`이 반드시 묶이는지 (`stop` 노드까지 거슬러 올라감).

    같은 문장 목록의 앞선 형제 문장이 반드시 묶거나(`_binds`), 감싸는 문장의
    머리가 묶으면 True입니다.
    """
    node = use
    while node in parents and node is not stop:
        parent, field, index = parents[node]
        if index is not None:
            siblings = getattr(parent, field)
            if any(_binds(s, name) for s in siblings[:index]):
                return True
        if _header_binds(parent, field, name):
            return True
        node = parent
    return False


_FUNC_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _scope_of(node, parents: dict):
    """`node`를 감싸는 가장 안쪽 함수·람다 (없으면 None = 모듈)."""
    while node in parents:
        node = parents[node][0]
        if isinstance(node, _FUNC_SCOPES):
            return node
    return None


def _scope_bindings(scope) -> tuple[set[str], set[str], set[str]]:
    """함수 범위의 (지역 이름, 매개변수, global·nonlocal 선언) — 중첩 범위 안은 제외."""
    args = scope.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    every += [x for x in (args.vararg, args.kwarg) if x]
    params = {a.arg for a in every}
    local: set[str] = set()
    declared: set[str] = set()
    stack = list(scope.body) if isinstance(scope.body, list) else [scope.body]
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.Global, ast.Nonlocal)):
            declared |= set(n.names)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            local.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            local |= _import_names(n)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            local.add(n.name)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(n.name)
            continue  # 중첩 범위의 본문은 그 범위에서 따로 봄
        if isinstance(n, (ast.Lambda, *_COMPS)):
            continue
        stack.extend(ast.iter_child_nodes(n))
    return local - declared, params, declared


def _comprehension_binds(use, name: str, parents: dict) -> bool:
    """`use`를 감싸는 컴프리헨션의 for 대상이 `name`인지."""
    node = use
    while node in parents:
        node = parents[node][0]
        if isinstance(node, _FUNC_SCOPES):
            return False
        if isinstance(node, _COMPS) and any(
            name in _target_names(g.target) for g in node.generators
        ):
            return True
    return False


def _module_names(tree) -> set[str]:
    """파일 어디에서든(조건부 포함) 묶이는 이름 전체."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out |= _import_names(n)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
    return out


def _name_status(tree, use, name: str, parents: dict) -> str:
    """이름을 읽는 위치 하나의 판정.

    "bound"(반드시 정의됨) · "undefined"(파일 어디에도 정의 없음) ·
    "unsure"(조건부 정의 등 코드로 단정 못 함).
    """
    import builtins

    if _comprehension_binds(use, name, parents):
        return "bound"
    scope = _scope_of(use, parents)
    deferred = False  # 함수 안의 사용: 바깥 이름은 호출 시점에 찾음
    while scope is not None:
        local, params, declared = _scope_bindings(scope)
        if name in declared:
            break  # global·nonlocal 선언 → 모듈 기준으로 확인
        if name in params:
            return "bound"
        if name in local:
            if not deferred:
                bound = _dominated(use, name, parents, scope)
            else:
                body = scope.body if isinstance(scope.body, list) else []
                bound = any(_binds(s, name) for s in body)
            return "bound" if bound else "unsure"
        deferred = True
        use, scope = scope, _scope_of(scope, parents)
    if any(
        isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names)
        for n in ast.walk(tree)
    ):
        return "unsure"  # `import *`가 무엇을 들여오는지 모름
    if deferred and any(_binds(s, name) for s in tree.body):
        return "bound"
    if not deferred and _dominated(use, name, parents, tree):
        return "bound"
    if hasattr(builtins, name) or name in {"__name__", "__file__", "__doc__"}:
        return "bound"
    return "unsure" if name in _module_names(tree) else "undefined"


def _claimed_uses(tree, finding: Finding) -> dict[str, list]:
    """지적이 말하는 이름(백틱·`name 'X'`)과, 지적 줄(없으면 ±3줄)에서 읽는 위치."""
    names = set(_BACKTICK_NAME_RE.findall(finding.message))
    names |= set(_QUOTED_NAME_RE.findall(finding.message))
    loads = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id in names
    ]
    out: dict[str, list] = {}
    for name in names:
        mine = [n for n in loads if n.id == name]
        exact = [n for n in mine if n.lineno == finding.line]
        near = [n for n in mine if abs(n.lineno - finding.line) <= 3]
        if exact or near:
            out[name] = exact or near
    return out


def _claim_statuses(source: str, finding: Finding) -> tuple[dict[str, str], str]:
    """파일 전체 소스에서 지적이 말한 이름별 판정과, 판정 못 한 사유."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}, "변경 후 파일이 파싱되지 않음"
    parents = _parents(tree)
    uses = _claimed_uses(tree, finding)
    if not uses:
        return {}, "지적 줄 근처에서 주장한 이름을 찾지 못함"
    statuses = {}
    for name, nodes in uses.items():
        found = {_name_status(tree, n, name, parents) for n in nodes}
        for status in ("undefined", "unsure", "bound"):
            if status in found:
                statuses[name] = status
                break
    return statuses, ""


def verify_name_claims(
    findings: list[Finding], py_files: list[dp.FileDiff]
) -> tuple[list[Finding], list[dict], list[dict]]:
    """이름 미정의(NameError·UnboundLocalError 등)를 주장하는 지적을 정적 분석으로 검증.

    결정적 검사입니다(LLM 없음). diff로 변경 후 파일 전체를 다시 만들어 `ast`로
    분석하고, 지적이 말한 이름을 지적 줄 근처에서 읽는 위치마다 판정합니다.
    - 같은 범위의 앞선 문장이 반드시 정의하거나(분기 한쪽만은 아님), 매개변수·
      for 대상·with as 등이 묶거나, 함수 안에서 쓰는 바깥 이름이 모듈에서 반드시
      정의되면 "bound" → 말한 이름이 모두 bound면 주장이 사실이 아니므로 **제거**.
    - 파일 어디에도 정의가 없으면 "undefined" → 지적을 그대로 둡니다(진짜 미정의).
    - 파일 일부만 보이거나, 파싱이 안 되거나, 이름을 못 찾거나, 조건부 정의라
      단정할 수 없으면 지적을 남기되 설명 앞에 "판단 필요"를 붙입니다.

    Returns:
        (유지할 지적, 제거 기록 `name_claim_dropped`, 판단 필요 기록 `name_claim_kept`)
    """
    from src.toolkit.ruff import reconstruct_file

    by_path = {f.path: f for f in py_files}
    kept, dropped, unsure = [], [], []
    for f in findings:
        target = by_path.get(f.file)
        if not claims_undefined_name(f) or target is None:
            kept.append(f)
            continue
        where = {"file": f.file, "line": f.line, "issue_type": f.issue_type}
        source, complete = reconstruct_file(target)
        if complete:
            statuses, reason = _claim_statuses(source, f)
        else:
            statuses, reason = {}, "파일 일부만 보여 이름 정의를 확인할 수 없음"
        if statuses and all(s == "bound" for s in statuses.values()):
            dropped.append(
                {
                    **where,
                    "severity": f.severity,
                    "names": sorted(statuses),
                    "reason": "이름이 사용 전에 반드시 정의됨 (정적 분석)",
                    "finding": f.model_dump(),
                }
            )
            continue
        if "undefined" in statuses.values():
            kept.append(f)  # 진짜 미정의 이름: 표시 없이 유지
            continue
        reason = reason or "조건부 정의 등으로 코드로 단정할 수 없음"
        unsure.append({**where, "names": sorted(statuses), "reason": reason})
        message = f.message
        if not message.startswith(NAME_UNSURE_PREFIX):
            message = NAME_UNSURE_PREFIX + message
        kept.append(f.model_copy(update={"message": message}))
    return kept, dropped, unsure


# ── 출력 검사: ruff 스타일 근거뿐인 bug·security 중복 (제출 전 수정, cd65c0f F401) ──

_RUFF_STYLE_ID = re.compile(r"^ruff:[EWFIN]\d+$")


def drop_ruff_style_duplicates(
    findings: list[Finding],
) -> tuple[list[Finding], list[dict]]:
    """근거가 ruff 스타일 코드(`ruff:E·W·F·I·N…`)뿐인 bug·security 지적이 같은 파일·줄의
    style 지적과 겹치면 뺍니다 (결정적, LLM 없음).

    버그 리뷰어가 F401 같은 ruff 위반을 bug로도 내면 화면에 같은 문제가 두 번 나오고
    요약 2줄의 버그 수가 늘어납니다(cd65c0f 리뷰). 코드 위치·문서 근거가 하나라도 있으면
    별개의 지적으로 보고 그대로 둡니다.

    Returns:
        (유지할 지적, 제거 기록 `ruff_style_dup_dropped` — 위치·근거·지적 전체)
    """
    style_at = {(f.file, f.line) for f in findings if f.issue_type == "style"}
    kept, dropped = [], []
    for f in findings:
        ids = [e.rule_id or "" for e in f.evidence]
        if (
            f.issue_type in {"bug", "security"}
            and ids
            and all(_RUFF_STYLE_ID.match(i) for i in ids)
            and (f.file, f.line) in style_at
        ):
            dropped.append(
                {
                    "file": f.file,
                    "line": f.line,
                    "issue_type": f.issue_type,
                    "rule_ids": ids,
                    "reason": "ruff 스타일 근거뿐이고 같은 줄 style 지적과 중복",
                    "finding": f.model_dump(),
                }
            )
            continue
        kept.append(f)
    return kept, dropped
