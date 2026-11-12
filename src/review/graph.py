"""메인 에이전트 그래프와 핵심 함수 `run_query` (CLAUDE.md 3번·3-1번).

흐름:
    입력 가드레일 → planner → 리뷰 팀(supervisor + 리뷰어 2종)
    → 출력 검증(근거 실재) → 종합(요약 5줄, 형식 오류 시 1회 재생성) → 렌더링
    → HITL(interrupt로 승인 대기) → 게시(승인 + 허용 목록일 때만, dry-run)
- 거절(.py 없음, 300줄 초과)이면 입력 가드레일에서 바로 렌더링으로 가며
  LLM을 부르지 않습니다.
- `run_query`는 항상 HITL의 `interrupt`까지 실행하고 "승인 대기"를 trace에 남긴 뒤
  돌아옵니다. 평가 실행기·CLI `--batch`는 여기서 끝,
  대화형 CLI는 `resume_review`로 재개합니다.
- 승인 여부와 게시 대상(허용 목록)은 그래프 코드가 결정하며 LLM 출력·diff 내용을 읽지
  않습니다 (프롬프트 인젝션에 대한 "코드로 강제" 층).

설계 메모:
- 그래프 상태에는 직렬화하기 쉬운 작은 값만 둡니다. 리뷰 한 건의 무거운 객체
  (파싱된 diff, 도구 기록, trace)는 `RunContext`에 두고 노드가 클로저로 씁니다.
  B2에서 체크포인터를 붙여도 상태 직렬화 문제가 생기지 않게 하려는 것입니다.
- 모델은 `llm` 인자로 주입합니다. 모델 하나를 주면 모든 역할이 같이 쓰고,
  dict(`supervisor`·`bug_reviewer`·`style_reviewer`·`synthesizer`)를 주면
  역할별로 씁니다. `None`이면 그때 `config.get_llm()`을 부릅니다.
  모듈 최상단에서는 모델을 만들지 않습니다.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from src.diff import parser as dp
from src.findings.schema import Finding, Review
from src.guardrails.input import check_input
from src.guardrails.output import (
    FALLBACK_SUGGESTION,
    check_output,
    cross_check_pep8,
    forbidden_suggestion,
    mentions_classic,
    removed_import_patterns,
    strip_classic_sentences,
    strip_classic_sentences_keep_code,
    verify_api_claims,
    verify_syntax_claims,
)
from src.guardrails.schema import InputCheck
from src.observability.trace import TraceRecorder
from src.observability.usage import ROLE_KEY, record_usage
from src.report.render import (
    is_line_length,
    line_length_detail,
    line_length_of,
    ordered_items,
    render_rejection,
    render_review,
)
from src.review.line_length import _LINE_TOO_LONG_RE, line_length_findings
from src.review.prompts import FIX_PROMPT, SYNTH_DIFF_CHARS, SYNTH_PROMPT
from src.review.schema import SuggestionFix, SummaryDraft
from src.review.summary import (
    action_fallback,
    action_violation,
    change_fallback,
    change_violation,
    limit_line_length_recs,
    scope_line,
    secbug_line,
    style_line,
    synth_payload,
)
from src.review.team import BUG, STYLE, Plan, plan_reviews, run_review_team
from src.shared import config, review_settings
from src.tools import github
from src.tools.agent_tools import ToolLog, build_tools, chunk_to_dict
from src.tools.github import github_post_comment
from src.tools.ruff import run_ruff

ROLES = ("supervisor", BUG, STYLE, "synthesizer")


FALLBACK_MESSAGE = "1.x에서 제거되었거나 권장되지 않는 API를 사용하고 있습니다."


THROTTLE_WORDS = ("Throttling", "TooManyRequests", "ServiceUnavailable")


def _invoke(
    chain, inputs: dict, trace: TraceRecorder, role: str = "기타", waits=(15, 30, 45)
):
    """체인 호출. Bedrock 호출 한도 오류는 기다렸다가 재시도합니다.

    호출 한도 오류는 출력 형식 오류가 아니므로 형식 오류 재생성 횟수에 넣지 않습니다
    (1차 평가 6번 케이스: 한도 오류가 형식 오류로 집계되어 실패 처리됨).
    `role`은 토큰 집계 구분(trace `usage` 이벤트)에 쓰는 이름입니다.
    """
    import time

    for wait in (*waits, None):
        try:
            return chain.invoke(inputs, config={"metadata": {ROLE_KEY: role}})
        except Exception as exc:
            if wait is None or not any(w in str(exc) for w in THROTTLE_WORDS):
                raise
            trace.add("error", error=f"호출 한도 대기 {wait}초: {str(exc)[:120]}")
            time.sleep(wait)


_DIFF_PATH_RE = re.compile(r"(\S+\.diff)\b")


@dataclass
class RunContext:
    """리뷰 한 건 동안 노드들이 함께 쓰는 객체."""

    diff_text: str
    title: str
    models: dict
    retriever: object
    trace: TraceRecorder
    tool_log: ToolLog
    check: InputCheck | None = None
    plan: Plan | None = None
    reviewer_findings: dict[str, list[Finding]] = field(default_factory=dict)
    # 리뷰어별 결과 수집 방식: agent / finalize / none (none이면 화면에 "확인 못 함")
    reviewer_status: dict[str, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)  # 출력 검증 통과분
    dropped: int = 0  # 근거 미확인으로 제거된 지적 수
    review: Review | None = None
    answer: str = ""
    thread_id: str = ""
    hitl_logged: bool = False
    post_result: dict = field(default_factory=dict)
    # (파일, 줄) → (ruff가 잰 길이, 기준): E501 묶음 표시용 (출력 검증에서 계산)
    line_lengths: dict = field(default_factory=dict)
    settings: review_settings.ReviewSettings = (
        review_settings.DEFAULT_SETTINGS
    )  # 적용 기준


class GraphState(TypedDict, total=False):
    """그래프 상태 (작은 값만)."""

    status: str  # "ok" | "rejected" | "failed"
    reason: str
    decision: dict  # HITL 결정 {"action": "approve"|"reject", "ignore": [번호], "repo"}


def _resolve_models(llm) -> dict:
    """llm 인자를 역할별 모델 dict로 바꿉니다. None이면 실제 모델을 한 번만 만듭니다."""
    if isinstance(llm, dict):
        missing = [r for r in ROLES if r not in llm]
        base = llm.get("default")
        if missing and base is None:
            base = config.get_llm()
        return {r: llm.get(r, base) for r in ROLES}
    model = llm if llm is not None else config.get_llm()
    return {r: model for r in ROLES}


_PEP8_LINE_CHUNK: dict | None = None


def _pep8_line_length_chunk() -> dict | None:
    """코드가 만든 줄 길이 지적의 doc 근거로 쓸 PEP 8 청크 (contexts에도 넣음)."""
    global _PEP8_LINE_CHUNK
    if _PEP8_LINE_CHUNK is None:
        from src.evidence.corpus import load_corpus

        docs = [d for d in load_corpus() if d.metadata["rule_id"] == E501_DOC_ID]
        _PEP8_LINE_CHUNK = chunk_to_dict(docs[0]) if docs else {}
    return _PEP8_LINE_CHUNK or None


E501_DOC_ID = "PEP8-maximum-line-length"


def _unchecked(ctx: RunContext) -> list[str]:
    """결과를 받지 못한(structured가 none인) 리뷰어 이름."""
    return [n for n, s in ctx.reviewer_status.items() if s == "none"]


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """같은 파일·줄·유형·근거 rule_id의 지적은 하나만 남깁니다 (리뷰어 간 중복)."""
    seen, out = set(), []
    for f in findings:
        rule_ids = tuple(sorted(e.rule_id or "" for e in f.evidence))
        key = (f.file, f.line, f.issue_type, rule_ids)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def build_graph(ctx: RunContext, checkpointer=None):
    """리뷰 한 건을 처리하는 그래프를 만듭니다 (노드는 ctx를 클로저로 씀)."""

    def input_guardrail(state: GraphState) -> GraphState:
        ctx.trace.add("node", name="input_guardrail")
        ctx.check = check_input(ctx.diff_text, ctx.settings.max_lines)
        ctx.trace.add(
            "guardrail",
            stage="input",
            status=ctx.check.status,
            reason=ctx.check.reason,
            skipped=ctx.check.skipped,
            masked=ctx.check.masked,
            security_findings=len(ctx.check.findings),
            py_changed_lines=ctx.check.py_changed_lines,
        )
        return {"status": ctx.check.status, "reason": ctx.check.reason}

    def planner(state: GraphState) -> GraphState:
        ctx.trace.add("node", name="planner")
        ctx.plan = plan_reviews(ctx.check.py_files)
        ctx.trace.add(
            "plan",
            reviewers=ctx.plan.reviewers,
            files=ctx.plan.files,
            reasons=ctx.plan.reasons,
        )
        return {}

    def review_team(state: GraphState) -> GraphState:
        ctx.trace.add("node", name="supervisor")
        tools = build_tools(
            ctx.check.py_files, ctx.retriever, ctx.tool_log, ctx.settings
        )
        ctx.reviewer_findings = run_review_team(
            ctx.plan,
            ctx.check.py_files,
            tools,
            ctx.models,
            ctx.trace,
            status=ctx.reviewer_status,
        )
        # 리뷰어별 결과 수집 방식과 결과를 받지 못한(none) 리뷰어 (화면 "확인 못 함")
        ctx.trace.add(
            "guardrail",
            stage="reviewers",
            structured=dict(ctx.reviewer_status),
            none=[n for n, s in ctx.reviewer_status.items() if s == "none"],
        )
        return {}

    patterns = removed_import_patterns()
    mapping_rows = "\n".join(
        line
        for line in (config.CORPUS_DIR / "deprecated_map.md")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("| `DEP-")
    )

    def _forbidden_rule(message: str, suggestion: str) -> str | None:
        """지적 전체 텍스트 검사: 고치는 방법의 코드에 제거된 API import가 있거나,
        설명·고치는 방법 어디에든 langchain_classic이 나오면 그 rule_id."""
        rule = forbidden_suggestion(suggestion, patterns)
        if rule is None and mentions_classic(f"{message}\n{suggestion}"):
            rule = "DEP-langchain_classic"
        return rule

    def _fix_forbidden_suggestion(finding: Finding) -> Finding:
        """지적 전체 텍스트에 금지된 권장이 있으면 최대 1회 다시 만들고, 그래도
        남으면 설명에서 해당 문장을 지우고 고치는 방법을 대체 문구로 바꿉니다
        (결정적 검사)."""
        rule = _forbidden_rule(finding.message, finding.suggestion)
        if rule is None:
            return finding
        message, suggestion = finding.message, finding.suggestion
        line_text = next(
            (
                dp.new_side_lines(f).get(finding.line, "")
                for f in ctx.check.py_files
                if f.path == finding.file
            ),
            "",
        )
        for attempt in range(review_settings.MAX_FORMAT_RETRIES):
            model = ctx.models[BUG]
            chain = FIX_PROMPT | model.with_structured_output(SuggestionFix)
            try:
                fixed: SuggestionFix = _invoke(
                    chain,
                    {
                        "message": message,
                        "rule_ids": [e.rule_id for e in finding.evidence],
                        "code": f"{finding.file}:{finding.line}  {line_text}",
                        "suggestion": suggestion,
                        "mapping": mapping_rows,
                    },
                    ctx.trace,
                    role="suggestion_fix",  # 고치는 방법 재생성 (토큰 따로 집계)
                )
                message, suggestion = fixed.message, fixed.suggestion
            except Exception as exc:  # 재생성 실패도 대체 문구로 처리
                ctx.trace.add("error", error=f"{type(exc).__name__}: {str(exc)[:200]}")
                break
            rule_after = _forbidden_rule(message, suggestion)
            ctx.trace.add(
                "guardrail",
                stage="suggestion",
                file=finding.file,
                line=finding.line,
                forbidden=rule,
                attempt=attempt + 1,
                still_forbidden=rule_after,
            )
            if rule_after is None:
                return finding.model_copy(
                    update={"message": message, "suggestion": suggestion}
                )
        ctx.trace.add(
            "guardrail",
            stage="suggestion",
            file=finding.file,
            line=finding.line,
            forbidden=rule,
            fallback=True,
        )
        # 원래 제안의 코드가 깨끗하면(설명 문장만 문제) 코드는 살리고 그 문장만 지움.
        # 코드 자체에 금지된 API가 있을 때만 고치는 방법을 대체 문구로 바꿈.
        code_ok = forbidden_suggestion(finding.suggestion, patterns) is None
        kept_suggestion = (
            strip_classic_sentences_keep_code(finding.suggestion) if code_ok else ""
        )
        return finding.model_copy(
            update={
                "message": strip_classic_sentences(finding.message) or FALLBACK_MESSAGE,
                "suggestion": kept_suggestion or FALLBACK_SUGGESTION,
            }
        )

    def output_validate(state: GraphState) -> GraphState:
        """근거 실재 확인: 이번 diff·검색·도구 출력에 없던 근거를 지웁니다."""
        ctx.trace.add("node", name="output_validate")
        raw: list[Finding] = []
        for name in ctx.plan.reviewers:
            raw += ctx.reviewer_findings.get(name, [])
        raw = _dedupe(raw)
        result = check_output(
            raw, ctx.check, ctx.tool_log.ruff_findings, set(ctx.tool_log.retrieved)
        )
        # ① 존재 검사 결과. 근거 확인 비율은 여기까지로만 계산 (C2 제거분은 섞지 않음)
        existed = len(_dedupe(list(ctx.check.findings) + result.kept))
        # ② 선택 작업 C2: PEP 8 근거가 그 줄의 ruff 위반과 맞지 않는 style 지적 제거.
        # ruff 기준은 리뷰어가 ruff_check를 불렀는지와 관계없이 diff로 다시 계산함.
        # C2 결과는 근거-지적 일치율·C2 제거 수로만 보고 (evaluation/README.md 3절).
        ruff_all = [r for f in ctx.check.py_files for r in run_ruff(f, ctx.settings)]
        ctx.line_lengths = {
            (r["file"], r["line"]): (int(m.group(1)), int(m.group(2)))
            for r in ruff_all
            if r["code"] == "E501" and (m := _LINE_TOO_LONG_RE.search(r["message"]))
        }
        kept, c2_removed = result.kept, []
        if review_settings.PEP8_CROSS_CHECK:
            truth = {(r["file"], r["line"], r["code"]) for r in ruff_all}
            kept, c2_removed = cross_check_pep8(kept, truth)
        # ④ 파싱 실패(SyntaxError 등)를 주장하는 지적은 변경 후 파일을 ast.parse로
        # 검증해 파싱되면 제거 (논리 버그 지적은 대상 아님). C2처럼 따로 보고.
        kept, syntax_dropped, syntax_kept = verify_syntax_claims(
            kept, ctx.check.py_files
        )
        # ⑥ API가 없다·제거됐다는 지적은 설치된 패키지에서 실제 import로 확인해,
        # import되면 제거 (매핑표의 "제거" API는 유지). C2처럼 따로 보고.
        kept, api_claim_dropped = verify_api_claims(kept, ctx.check.py_files)
        # 입력 가드레일의 security 지적은 가드레일이 직접 만든 것이라 그대로 둠
        kept = [_fix_forbidden_suggestion(f) for f in kept]
        # ③ 줄 길이(E501)는 리뷰어 지적 대신 ruff 결과로 코드가 만듦 (숫자도 ruff 값)
        doc = _pep8_line_length_chunk()
        if doc and any(r["code"] == "E501" for r in ruff_all):
            ctx.tool_log.retrieved.setdefault(doc["rule_id"], doc)
        long_lines = line_length_findings(ruff_all, ctx.settings, doc)
        replaced = [f for f in kept if is_line_length(f)]
        kept = [f for f in kept if not is_line_length(f)]
        ctx.findings = _dedupe(list(ctx.check.findings) + long_lines + kept)
        ctx.dropped = len(result.dropped)  # 존재 검사 제거분만 (화면 안내와 같은 값)
        dropped = [
            {
                "file": d["finding"].file,
                "line": d["finding"].line,
                "issue_type": d["finding"].issue_type,
                "reason": d["reason"],
            }
            for d in result.dropped
        ]
        # 근거-지적 일치율(evaluation/README.md)은 출력 검증 전 지적으로 계산하므로
        # 리뷰어가 낸 지적을 그대로 남김 (마스킹 후 diff 기준 위치·근거 ID만)
        raw_log = [
            {
                "file": f.file,
                "line": f.line,
                "issue_type": f.issue_type,
                "rule_ids": [e.rule_id for e in f.evidence if e.rule_id],
            }
            for f in raw
        ]
        ctx.trace.add(
            "guardrail",
            stage="output",
            before=len(ctx.check.findings) + len(raw),
            after=existed,  # 존재 검사를 통과한 지적 수
            after_c2=len(ctx.findings),  # C2까지 거친 최종 지적 수
            dropped=dropped,
            raw_findings=raw_log,
            c2_removed=c2_removed,  # C2가 제거한 지적과 사유
            # ④ 파싱 실패 주장 검증: 제거한 지적(전체 포함)과
            # 검증 불가·실제 파싱 실패로 유지한 지적
            syntax_dropped=syntax_dropped,
            syntax_kept=syntax_kept,
            # ⑥ API 존재 주장 검증: import되는데 없다고 한 지적(대상·패키지 버전·전체)
            api_claim_dropped=api_claim_dropped,
            line_length_by_code=len(long_lines),  # ruff로 코드가 만든 줄 길이 지적
            reviewer_line_length_dropped=len(replaced),  # 리뷰어가 낸 줄 길이 지적
        )
        return {}

    def synthesize(state: GraphState) -> GraphState:
        """요약 5줄·추천 개선안.

        2·3·4줄(보안·버그, 스타일, 범위·기준)은 코드가 지적 목록과 설정에서 만들고,
        LLM(LCEL)은 1줄(변경 내용)·5줄(할 일)·추천 개선안만 씁니다.
        LLM 줄이 규칙을 어기면 1회 재생성, 그래도 어기면 그 줄만 코드 문장으로 바꿉니다.
        형식 오류는 최대 1회 재생성 후 실패 처리합니다.
        """
        ctx.trace.add("node", name="synthesize")
        model = ctx.models["synthesizer"]
        chain = SYNTH_PROMPT | model.with_structured_output(SummaryDraft)
        inputs = {
            "files": ", ".join(ctx.plan.files),
            "findings": json.dumps(synth_payload(ctx.findings), ensure_ascii=False),
            "diff": ctx.check.masked_diff()[:SYNTH_DIFF_CHARS],
            "criteria": ctx.settings.label(),
        }
        # 줄 길이 문장에 쓸 수 있는 숫자: 적용 기준과 ruff가 잰 길이·기준
        lengths = {ctx.settings.line_length} | {
            n for pair in ctx.line_lengths.values() for n in pair
        }
        draft: SummaryDraft | None = None
        problems: dict[str, str] = {}
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(review_settings.MAX_FORMAT_RETRIES + 1):
            attempts = attempt + 1
            try:
                # 첫 호출과 재생성(형식 오류·규칙 위반) 토큰은 따로 집계
                role = "synthesize" if attempt == 0 else "synthesize:retry"
                draft = _invoke(chain, inputs, ctx.trace, role=role)
                if draft is None:  # 모델이 구조화 출력 도구를 부르지 않음 (gpt-oss)
                    raise ValueError("구조화 출력이 비어 있습니다")
            except Exception as exc:  # 형식 오류 (스키마 검증·파싱 실패)
                last_error = exc
                ctx.trace.add(
                    "guardrail",
                    stage="format",
                    attempt=attempts,
                    error=f"{type(exc).__name__}: {str(exc)[:200]}",
                )
                continue
            problems = {
                k: v
                for k, v in {
                    "change": change_violation(draft.change, lengths),
                    "action": action_violation(draft.action, ctx.findings, lengths),
                    "recommendations": "langchain_classic 권장"
                    if mentions_classic(" ".join(draft.recommendations))
                    else None,
                }.items()
                if v
            }
            if not problems:
                break
            ctx.trace.add(
                "guardrail", stage="summary_rules", attempt=attempts, problems=problems
            )
        if draft is None:
            reason = (
                "출력 형식 오류가 재생성 후에도 남아 리뷰를 실패로 처리했습니다 "
                f"({type(last_error).__name__})"
            )
            return {"status": "failed", "reason": reason}
        change = draft.change.strip()
        action = draft.action.strip()
        if "change" in problems:
            change = change_fallback(ctx.check.py_files)
        if "action" in problems:
            action = action_fallback(ctx.findings)
        recs = limit_line_length_recs(
            [r for r in draft.recommendations if not mentions_classic(r)],
            ctx.findings,
        )
        summary = [
            " ".join(change.split()),
            secbug_line(ctx.findings),
            style_line(ctx.findings),
            scope_line(ctx.plan.files, ctx.check.skipped, ctx.settings),
            " ".join(action.split()),
        ]
        # 대체 비율 집계용 기록 (round3 리포트): 어떤 LLM 줄이 코드 문장으로 바뀌었나
        ctx.trace.add(
            "guardrail",
            stage="summary",
            attempts=attempts,
            replaced=[k for k in ("change", "action") if k in problems],
            problems=problems,
        )
        if "recommendations" in problems:
            ctx.trace.add("guardrail", stage="classic", fallback=True)
        ctx.review = Review(
            summary=summary, findings=ctx.findings, recommendations=recs
        )
        return {}

    def render(state: GraphState) -> GraphState:
        """docs/output_example.md 형식으로 렌더링 (src/report/render.py, LLM 없음)."""
        ctx.trace.add("node", name="render")
        if state.get("status") in {"rejected", "failed"}:
            ctx.answer = render_rejection(ctx.title, state.get("reason", ""))
            return {}
        titles = {rid: c["title"] for rid, c in ctx.tool_log.retrieved.items()}
        ctx.answer = render_review(
            ctx.review,
            ctx.check.py_files,
            title=ctx.title,
            skipped=ctx.check.skipped,
            masked=ctx.check.masked,
            dropped=ctx.dropped,
            doc_titles=titles,
            line_lengths=ctx.line_lengths,
            criteria=ctx.settings.label(),
            unchecked=_unchecked(ctx),
        )
        # 줄 길이 상세(코드 줄·주석 구분, ruff 길이)는 화면에 없고 --verbose에서만
        long_lines = line_length_of(ctx.review)
        if long_lines:
            ctx.trace.add(
                "guardrail",
                stage="line_length",
                count=len(long_lines),
                detail=line_length_detail(
                    long_lines, ctx.check.py_files, ctx.line_lengths
                ),
            )
        return {}

    def hitl(state: GraphState) -> GraphState:
        """게시 전 사람 승인 대기 (langgraph interrupt). 재개 전까지 여기서 멈춤."""
        if not ctx.hitl_logged:  # 재개 시 노드가 다시 실행되므로 한 번만 기록
            ctx.trace.add("node", name="hitl")
            ctx.trace.add(
                "hitl",
                status="waiting",
                thread_id=ctx.thread_id,
                findings=len(ctx.review.findings),
            )
            ctx.hitl_logged = True
        decision = interrupt(
            {"question": "PR에 코멘트로 게시할까요?", "thread_id": ctx.thread_id}
        )
        decision = decision if isinstance(decision, dict) else {"action": "reject"}
        ctx.trace.add(
            "hitl",
            status="decided",
            action=decision.get("action"),
            ignore=decision.get("ignore", []),
            repo=decision.get("repo"),
        )
        return {"decision": decision}

    def post(state: GraphState) -> GraphState:
        """승인 + 허용 목록 저장소일 때만 게시 (dry-run). 판단은 코드로만 함."""
        ctx.trace.add("node", name="post")
        decision = state.get("decision") or {}
        repo = decision.get("repo") or ""
        if decision.get("action") != "approve":
            ctx.post_result = {"status": "rejected", "reason": "사용자가 게시를 거절함"}
        elif repo not in github.allowed_post_repos():
            ctx.post_result = {
                "status": "blocked",
                "reason": f"{repo or '(저장소 없음)'}은 게시 허용 목록에 없음",
            }
        else:
            ignore = set(decision.get("ignore", []))
            # 화면 번호는 표시 항목 단위
            # (E501 묶음 번호를 빼면 그 파일의 E501 전부 제외)
            # 화면 번호는 번호 붙은 지적만. 줄 길이(E501)는 번호가 없는 "참고"라
            # 무시 대상이 아니고 게시 본문에도 같은 "## 참고"로 그대로 남음
            kept = [
                f
                for i, item in enumerate(ordered_items(ctx.review), 1)
                if i not in ignore
                for f in item
            ] + line_length_of(ctx.review)
            body = render_review(
                ctx.review.model_copy(update={"findings": kept}),
                ctx.check.py_files,
                title=ctx.title,
                skipped=ctx.check.skipped,
                masked=ctx.check.masked,
                dropped=ctx.dropped,
                doc_titles={r: c["title"] for r, c in ctx.tool_log.retrieved.items()},
                line_lengths=ctx.line_lengths,
                criteria=ctx.settings.label(),
                unchecked=_unchecked(ctx),
            )
            result = github_post_comment(repo, body, ctx.tool_log)
            ctx.post_result = {"status": "posted", **result}
        ctx.trace.add(
            "post", **{k: v for k, v in ctx.post_result.items() if k != "body"}
        )
        return {}

    graph = StateGraph(GraphState)
    for name, fn in [
        ("input_guardrail", input_guardrail),
        ("planner", planner),
        ("review_team", review_team),
        ("output_validate", output_validate),
        ("synthesize", synthesize),
        ("render", render),
        ("hitl", hitl),
        ("post", post),
    ]:
        graph.add_node(name, fn)
    graph.add_edge(START, "input_guardrail")
    graph.add_conditional_edges(
        "input_guardrail",
        lambda s: "render" if s.get("status") == "rejected" else "planner",
        {"render": "render", "planner": "planner"},
    )
    graph.add_edge("planner", "review_team")
    graph.add_edge("review_team", "output_validate")
    graph.add_edge("output_validate", "synthesize")
    graph.add_edge("synthesize", "render")
    graph.add_conditional_edges(
        "render",
        lambda s: END if s.get("status") in {"rejected", "failed"} else "hitl",
        {"hitl": "hitl", END: END},
    )
    graph.add_edge("hitl", "post")
    graph.add_edge("post", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


# 승인 대기 중인 리뷰 (같은 프로세스 안에서 resume_review로 재개)
_PENDING: dict[str, tuple] = {}


def extract_diff_path(question: str) -> Path:
    """질의 문장에서 diff 파일 경로를 꺼냅니다 (CSV `input` 형식)."""
    match = _DIFF_PATH_RE.search(question)
    if not match:
        raise ValueError("질의에서 diff 파일 경로(*.diff)를 찾지 못했습니다.")
    path = Path(match.group(1))
    return path if path.is_absolute() else config.ROOT_DIR / path


def run_query(
    question: str,
    *,
    llm=None,
    thread_id: str | None = None,
    retriever=None,
    repo_dir: str | None = None,
    max_lines: int | None = None,
) -> dict:
    """질의(diff 경로를 담은 문장)를 받아 리뷰 결과를 돌려줍니다.

    Returns:
        {"answer": Markdown 리뷰, "contexts": 근거로 쓰인 청크 목록,
         "trace": 이벤트 목록}
    Args:
        llm: 모델 하나 또는 역할별 dict. None이면 실제 모델(config.get_llm()).
        thread_id: HITL 재개용 식별자. None이면 새로 만듦 (trace의 hitl 이벤트에 기록).
        retriever: 하이브리드 검색기. None이면 실제 색인(config.get_embeddings())을 씀.
        repo_dir: 리뷰 대상 저장소 폴더(있으면 그 저장소의 ruff·citereview 설정을 따름).
        max_lines: 변경 줄 수 한도(1~500). None이면 저장소 설정 > 기본 300.
            평가 실행은 둘 다 넘기지 않으므로 항상 기본값입니다.
    """
    thread_id = thread_id or f"rv-{uuid.uuid4().hex[:8]}"
    trace = TraceRecorder()
    settings = review_settings.load_review_settings(repo_dir, max_lines)
    trace.add(
        "start", question=question, thread_id=thread_id, criteria=settings.label()
    )
    diff_path = extract_diff_path(question)
    diff_text = diff_path.read_text(encoding="utf-8")

    # 거절 대상이면 모델·검색기를 만들지 않음 (LLM 호출 전에 끝냄)
    precheck = check_input(diff_text, settings.max_lines)
    if precheck.status == "rejected":
        models, retriever = {}, None
    else:
        models = _resolve_models(llm)
        if retriever is None:
            from src.evidence.search import build_retriever

            retriever = build_retriever()

    try:
        title = diff_path.relative_to(config.ROOT_DIR).as_posix()
    except ValueError:
        title = diff_path.name
    ctx = RunContext(
        diff_text=diff_text,
        title=title,
        models=models,
        retriever=retriever,
        trace=trace,
        tool_log=ToolLog(trace=trace),
        thread_id=thread_id,
        settings=settings,
    )
    graph = build_graph(ctx)
    # 모델 호출 토큰을 역할별로 기록 (trace `usage` 이벤트, 거절 케이스는 0회)
    with record_usage() as usage:
        try:
            graph.invoke({}, config={"configurable": {"thread_id": thread_id}})
            if ctx.hitl_logged:
                _PENDING[thread_id] = (graph, ctx)
        except Exception as exc:  # 리뷰 실패도 trace에 남기고 결과 형식은 유지
            trace.add("error", error=f"{type(exc).__name__}: {str(exc)[:300]}")
            ctx.answer = ctx.answer or f"리뷰 중 오류가 났습니다: {type(exc).__name__}"
    trace.add("usage", **usage.summary())
    if ctx.review is not None:
        trace.add("review", review=ctx.review.model_dump())
    trace.add("end")
    return {
        "answer": ctx.answer,
        "contexts": list(ctx.tool_log.retrieved.values()),
        "trace": trace.events,
    }


def resume_review(thread_id: str, decision: dict) -> dict:
    """승인 대기 중인 리뷰를 사람의 결정으로 재개합니다 (대화형 CLI용).

    Args:
        decision: {"action": "approve"|"reject", "ignore": [화면 번호],
                   "repo": "owner/repo"}
    Returns:
        {"post": 게시 결과, "trace": 전체 이벤트 목록}
    """
    graph, ctx = _PENDING.pop(thread_id)
    graph.invoke(
        Command(resume=decision), config={"configurable": {"thread_id": thread_id}}
    )
    ctx.trace.add("end")
    return {"post": ctx.post_result, "trace": ctx.trace.events}
