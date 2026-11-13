"""리뷰 팀: 규칙 기반 planner + 리뷰어 2종(버그·스타일, `create_agent`) + supervisor.

- planner는 LLM 없이 규칙으로 호출할 리뷰어와 파일 범위를 정합니다 (Plan-Execute).
- 리뷰어는 `langchain.agents.create_agent`로 만들고,
  도구(`ruff_check`·`search_evidence`)를 스스로 고릅니다 (ReAct).
  결과는 `ToolStrategy(ReviewerFindings)` 구조화 출력입니다.
- supervisor는 `langgraph_supervisor.create_supervisor`로 리뷰어에게 일을 넘깁니다.
  supervisor가 계획된 리뷰어를 빠뜨려도, 끝난 뒤 빠진 리뷰어를 코드가 직접 실행합니다
  (planner의 계획이 항상 실행되도록 보장).
- 보안: 별도 보안 리뷰어는 없습니다. 비밀값·PII·인젝션은 입력 가드레일 정규식이,
  `eval`·`subprocess` 같은 판단형 보안 문제는 버그 리뷰어가 `security`로 냅니다.
- diff는 `<diff>` 구분자 안에 "검토 대상 데이터"로만 넣고, 그 안의 지시문은 따르지 않게
  시스템 프롬프트에 적습니다 (SERVICE.md 4번 정책 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph_supervisor import create_supervisor

from src.diff import parser as dp
from src.findings.schema import Finding
from src.guardrails.middleware import mask_messages, masking_middleware
from src.observability.usage import ROLE_KEY
from src.review.prompts import (
    FINALIZE_REQUEST,
    STYLE_PROMPT,
    SUPERVISOR_PROMPT,
    bug_prompt,
)
from src.review.schema import ReviewerFindings
from src.shared import review_settings

BUG = "bug_reviewer"


STYLE = "style_reviewer"


MAX_MODEL_CALLS = 8  # 리뷰어 한 명이 한 리뷰에서 모델을 부를 수 있는 횟수 상한


@dataclass
class Plan:
    """planner 결과."""

    reviewers: list[str]
    files: list[str]
    reasons: dict[str, str] = field(default_factory=dict)


def plan_reviews(py_files: list[dp.FileDiff]) -> Plan:
    """규칙 기반 planner: 어떤 리뷰어를 어떤 파일 범위로 부를지 정합니다 (LLM 없음).

    - 리뷰 대상 파일: 추가 줄이 있는 .py 파일 (삭제만 있는 파일도 삭제 지점 버그가
      있을 수 있어 포함)
    - 버그 리뷰어: 항상 (삭제로 생긴 버그·보안 포함)
    - 스타일 리뷰어: 추가된 줄이 하나라도 있을 때만 (삭제만 있으면 스타일 대상이 없음)
    """
    files = [f.path for f in py_files]
    reviewers = [BUG]
    reasons = {BUG: "모든 .py 변경에 실행"}
    if any(f.added_count for f in py_files):
        reviewers.append(STYLE)
        reasons[STYLE] = "추가된 줄이 있음"
    return Plan(reviewers=reviewers, files=files, reasons=reasons)


def build_reviewer(model, tools: list, name: str):
    """create_agent로 리뷰어 하나를 만듭니다 (구조화 출력 + 호출 횟수 제한)."""
    prompt = bug_prompt() if name == BUG else STYLE_PROMPT
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=prompt,
        response_format=ToolStrategy(ReviewerFindings),
        middleware=[
            masking_middleware(),
            ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
        ],
        name=name,
    )


def _task_message(numbered_diff: str, files: list[str]) -> str:
    return (
        f"리뷰 범위 파일: {', '.join(files)}\n"
        "아래 diff를 리뷰하고 ReviewerFindings로 답하세요.\n"
        f"<diff>\n{numbered_diff}\n</diff>"
    )


def _message_text(message) -> str:
    """AI 메시지의 일반 글 부분만 (gpt-oss의 추론 블록은 제외)."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    return "\n".join(
        p.get("text", "")
        for p in content
        if isinstance(p, dict) and p.get("type") == "text"
    )


def parse_findings_text(text: str) -> ReviewerFindings | None:
    """글로 온 JSON을 ReviewerFindings로 파싱합니다 (결정적, LLM 없음).

    gpt-oss는 결과 도구를 부르는 대신 `{"findings": [...]}`를 글로 답할 때가 있습니다
    (round3: 평가 21번, day03·day04 커밋 리뷰). ```json 울타리가 있어도 되고,
    지적 목록만 `[...]`로 온 경우도 받습니다. 스키마에 맞지 않으면 None.
    """
    import json

    from pydantic import ValidationError

    text = (text or "").strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start == -1 or end <= start:
            continue
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, list):
                data = {"findings": data}
            if isinstance(data, dict) and "findings" in data:
                return ReviewerFindings.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            continue
    return None


def _finalize(model, messages: list, name: str) -> tuple[ReviewerFindings | None, str]:
    """리뷰어가 결과 도구(ReviewerFindings)를 부르지 않고 끝났을 때의 보완 단계.

    gpt-oss는 tool_choice="any"를 받아도 강제하지 않아 결과 도구를 건너뛰고, 결과를
    JSON **글**로 답할 때가 있습니다. 순서:
    1. 리뷰어의 마지막 답이 JSON 글이면 그대로 파싱 (모델 호출 없음) → "agent_text"
    2. 아니면 대화 기록(마스킹 후)을 주고 구조화 출력으로 한 번 더 묻기 → "finalize"
    3. 그 답도 도구 없이 JSON 글이면 파싱 → "finalize_text"
    4. 모두 실패할 때만 None → "none" (지적 0건, 화면에 "결과 없음(확인 못 함)")
    """
    last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    parsed = parse_findings_text(_message_text(last)) if last is not None else None
    if parsed is not None:
        return parsed, "agent_text"
    try:
        chain = model.with_structured_output(ReviewerFindings, include_raw=True)
        out = chain.invoke(
            mask_messages(list(messages) + [HumanMessage(content=FINALIZE_REQUEST)]),
            config={"metadata": {ROLE_KEY: f"{name}:finalize"}},  # 토큰 따로 집계
        )
    except Exception:  # 호출·형식 오류도 보완 실패로 봄
        return None, "none"
    if out.get("parsed") is not None:
        return out["parsed"], "finalize"
    parsed = parse_findings_text(_message_text(out.get("raw")))
    return (parsed, "finalize_text") if parsed is not None else (None, "none")


def _wrap_reviewer(
    agent, model, name: str, task: str, collector: dict, trace, status: dict
):
    """리뷰어를 supervisor가 부를 수 있는 그래프로 감쌉니다.

    supervisor에게는 "지적 N건 기록" 메시지만 돌려주고,
    구조화 결과는 collector에, 결과를 받은 방식(agent/finalize/none)은 status에
    모읍니다 (supervisor 대화 기록에서 구조화 출력을 다시 파싱하지 않기 위해).
    """

    def run(state: MessagesState) -> dict:
        out = agent.invoke(
            {"messages": [HumanMessage(content=task)]},
            config={"metadata": {ROLE_KEY: name}},  # 토큰을 리뷰어별로 집계
        )
        structured = out.get("structured_response")
        source = "agent"
        if structured is None:
            structured, source = _finalize(model, out["messages"], name)
        result = structured or ReviewerFindings()
        collector[name] = list(result.findings)
        status[name] = source
        if trace is not None:
            # structured: agent(결과 도구) / agent_text(리뷰어 답의 JSON 글) /
            # finalize(보완 단계 도구) / finalize_text(보완 답의 JSON 글) /
            # none(모두 실패, 0건)
            trace.add(
                "reviewer",
                name=name,
                findings=len(result.findings),
                structured=source,
            )
        msg = f"{name}: 지적 {len(result.findings)}건을 기록했습니다."
        return {"messages": [AIMessage(content=msg, name=name)]}

    graph = StateGraph(MessagesState)
    graph.add_node("run", run)
    graph.add_edge(START, "run")
    graph.add_edge("run", END)
    return graph.compile(name=name)


def run_review_team(
    plan: Plan,
    py_files: list[dp.FileDiff],
    tools: list,
    models: dict,
    trace=None,
    status: dict[str, str] | None = None,
) -> dict[str, list[Finding]]:
    """supervisor로 계획된 리뷰어를 실행하고, 리뷰어별 지적 목록을 돌려줍니다.

    Args:
        models: {"supervisor": ..., "bug_reviewer": ..., "style_reviewer": ...}
        status: 주면 리뷰어별 결과 수집 방식(agent/finalize/none)을 채웁니다.
    """
    status = {} if status is None else status
    scope = [f for f in py_files if f.path in plan.files]
    to_text = (
        dp.focused_numbered_text
        if review_settings.REVIEWER_DIFF_CONTEXT == "focused"
        else dp.numbered_text
    )
    task = _task_message(to_text(scope), plan.files)
    collector: dict[str, list[Finding]] = {}
    workers = {
        name: _wrap_reviewer(
            build_reviewer(models[name], tools, name),
            models[name],
            name,
            task,
            collector,
            trace,
            status,
        )
        for name in plan.reviewers
    }
    supervisor = create_supervisor(
        list(workers.values()), model=models["supervisor"], prompt=SUPERVISOR_PROMPT
    ).compile()
    request = f"다음 리뷰어에게 순서대로 한 번씩 넘기세요: {', '.join(plan.reviewers)}"
    supervisor.invoke(
        {"messages": [HumanMessage(content=request)]},
        config={"recursion_limit": 25, "metadata": {ROLE_KEY: "supervisor"}},
    )
    # supervisor가 빠뜨린 리뷰어는 코드가 직접 실행 (계획 실행 보장)
    for name in plan.reviewers:
        if name not in collector:
            if trace is not None:
                trace.add("node", name=f"{name}(supervisor 누락 → 직접 실행)")
            workers[name].invoke({"messages": []})
    return collector
