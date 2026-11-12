"""src/agent.py 배선 테스트 (TASKS A4). 가짜 모델만 씁니다 (실제 LLM 호출 없음).

테스트 diff는 교육용 더미 데이터이며, AWS 키는 AWS 문서의 예시 형식 가짜 값입니다.
"""

import json

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from src import config
from src.agent import run_query
from src.retriever import build_retriever
from tests.fakes import call, fake, handoff

SUMMARY = ["요약 1", "요약 2", "요약 3", "요약 4", "요약 5"]
# AWS 문서 예시 형식의 가짜 키 (소스 파일 자체가 비밀값 탐지에 걸리지 않게 이어 붙임)
FAKE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_EMAIL = "hong.gildong@citereview-dummy.kr"


@pytest.fixture(scope="module")
def retriever(tmp_path_factory):
    return build_retriever(
        embeddings=DeterministicFakeEmbedding(size=32),
        persist_directory=str(tmp_path_factory.mktemp("chroma")),
        collection_name="test_agent",
    )


def _models(bug_findings, style_findings=()):
    """역할별 가짜 모델.

    supervisor가 두 리뷰어에게 넘기고, 각 리뷰어는 도구 1회 + 결과.
    """
    return {
        "supervisor": fake(
            handoff("bug_reviewer"), handoff("style_reviewer"), "리뷰 완료"
        ),
        "bug_reviewer": fake(
            call("search_evidence", query="from langchain.chains import LLMChain"),
            call("ReviewerFindings", findings=list(bug_findings)),
        ),
        "style_reviewer": fake(
            call("ruff_check", file=""),
            call("ReviewerFindings", findings=list(style_findings)),
        ),
        "synthesizer": fake(
            call(
                "ReviewSummary", summary=SUMMARY, recommendations=["구버전 import 정리"]
            )
        ),
    }


def test_run_query_returns_three_keys_and_trace(retriever):
    finding = {
        "issue_type": "bug",
        "file": "app/chain.py",
        "line": 12,
        "severity": "high",
        "message": "langchain.chains는 1.x에서 제거되어 ImportError가 납니다.",
        # 가짜 임베딩은 검색 결과가 무작위라 doc 근거는 지워질 수 있음
        # → code 근거도 붙임
        "evidence": [
            {
                "type": "doc",
                "source": "data/corpus/deprecated_map.md",
                "rule_id": "DEP-LLMChain",
            },
            {"type": "code", "source": "app/chain.py:12", "rule_id": None},
        ],
        "suggestion": "LCEL로 바꿉니다.",
    }
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘",
        llm=_models([finding]),
        retriever=retriever,
    )
    assert set(out) == {"answer", "contexts", "trace"}
    types = [e["type"] for e in out["trace"]]
    assert types[0] == "start" and types[-1] == "end" and "error" not in types
    tools = [e["name"] for e in out["trace"] if e["type"] == "tool"]
    assert tools == ["search_evidence", "ruff_check"]
    assert all("elapsed_ms" in e and "t" in e for e in out["trace"])
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert review["summary"] == SUMMARY
    assert [f["line"] for f in review["findings"]] == [12]
    assert out["contexts"]
    assert all({"rule_id", "text"} <= set(c) for c in out["contexts"])
    assert "app/chain.py:12" in out["answer"]


def test_secret_is_masked_before_llm_and_reported(tmp_path, retriever):
    diff = tmp_path / "secret.diff"
    diff.write_text(
        "diff --git a/app/config.py b/app/config.py\n"
        "--- /dev/null\n+++ b/app/config.py\n@@ -0,0 +1,3 @@\n"
        '+"""설정 (교육용 더미 데이터입니다)."""\n'
        f'+AWS_KEY = "{FAKE_KEY}"\n'
        f"+# 문의: {FAKE_EMAIL}\n",
        encoding="utf-8",
    )
    models = _models([])
    out = run_query(f"{diff} 를 리뷰해줘", llm=models, retriever=retriever)
    dumped = json.dumps(out, ensure_ascii=False)
    assert FAKE_KEY not in dumped and FAKE_EMAIL not in dumped
    seen = json.dumps([m.seen for m in models.values()], ensure_ascii=False)
    assert FAKE_KEY not in seen and FAKE_EMAIL not in seen
    assert "[MASKED:aws-access-key]" in seen  # 리뷰어는 마스킹 표시만 받음
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    rules = {(f["line"], f["evidence"][0]["rule_id"]) for f in review["findings"]}
    assert rules == {(2, "secret-scanner:aws-access-key"), (3, "pii-scanner:email")}


def test_java_only_diff_is_rejected_without_llm(tmp_path):
    diff = tmp_path / "java.diff"
    diff.write_text(
        "diff --git a/src/App.java b/src/App.java\n--- /dev/null\n+++ b/src/App.java\n"
        "@@ -0,0 +1,1 @@\n+class App {}\n",
        encoding="utf-8",
    )
    models = {
        r: fake()
        for r in ("supervisor", "bug_reviewer", "style_reviewer", "synthesizer")
    }
    out = run_query(f"{diff} 를 리뷰해줘", llm=models)
    assert "거절" in out["answer"] and "Python" in out["answer"]
    assert all(not m.seen for m in models.values())
    assert [e["type"] for e in out["trace"] if e["type"] == "tool"] == []


def test_output_check_drops_unverified_evidence():
    """출력 검증: 검색하지 않은 문서·없는 줄·없는 ruff 코드를 인용한 근거는 지운다."""
    from src.guardrails import check_input, check_output
    from src.schema import Finding

    text = (config.EVAL_DIFFS_DIR / "03_llmchain.diff").read_text(encoding="utf-8")
    check = check_input(text)

    def finding(line, *evidence):
        return Finding(
            issue_type="bug",
            file="app/chain.py",
            line=line,
            severity="high",
            message="m",
            evidence=list(evidence),
            suggestion="s",
        )

    doc = {"type": "doc", "source": "x", "rule_id": "DEP-LLMChain"}
    code = {"type": "code", "source": "app/chain.py:12"}
    ruff = {"type": "tool", "source": "ruff", "rule_id": "ruff:E501"}
    result = check_output(
        [
            finding(12, doc),
            finding(12, doc, code),
            finding(99, code),
            finding(12, ruff),
        ],
        check,
        ruff_findings=[],
        retrieved={"PEP8-imports"},
    )
    assert len(result.kept) == 1  # code 근거가 남은 두 번째 지적만
    assert [e.type for e in result.kept[0].evidence] == ["code"]
    reasons = [d["reason"] for d in result.dropped]
    assert "검색 결과에 없음" in reasons[0]
    assert "diff에 없음" in reasons[1]
    assert "ruff 출력에 없음" in reasons[2]


def test_forbidden_suggestion_is_regenerated_then_falls_back(retriever):
    """고치는 방법에 langchain_classic이 있으면 1회 재생성, 그래도 남으면 대체 문구."""
    from src.guardrails import FALLBACK_SUGGESTION

    BAD_FIX = "```python\nfrom langchain_classic.chains import LLMChain\n```"  # noqa: N806

    def bad_finding(line):
        return {
            "issue_type": "bug",
            "file": "app/chain.py",
            "line": line,
            "severity": "high",
            "message": "제거된 API입니다.",
            "evidence": [{"type": "code", "source": f"app/chain.py:{line}"}],
            "suggestion": BAD_FIX,
        }

    models = _models([bad_finding(12), bad_finding(31)])
    # 버그 리뷰어 모델이 재생성도 맡음: 12행은 고쳐지고, 31행은 또 금지 API를 냄
    models["bug_reviewer"].messages = iter(
        list(models["bug_reviewer"].messages)
        + [
            fake(
                call(
                    "SuggestionFix",
                    message="제거된 API입니다.",
                    suggestion="```python\nchain = prompt | llm\n```",
                )
            ).messages.__next__(),
            fake(
                call(
                    "SuggestionFix",
                    message="제거된 API입니다.",
                    suggestion="```python\nfrom langchain_classic import hub\n```",
                )
            ).messages.__next__(),
        ]
    )
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    by_line = {f["line"]: f["suggestion"] for f in review["findings"]}
    assert "chain = prompt | llm" in by_line[12]
    assert by_line[31] == FALLBACK_SUGGESTION
    assert "langchain_classic" not in out["answer"]
    events = [e for e in out["trace"] if e.get("stage") == "suggestion"]
    assert any(e.get("fallback") for e in events)


def test_classic_recommendation_in_prose_is_removed(retriever):
    """설명·요약·추천 개선안의 langchain_classic 권장 문장도 지운다 (1차 평가 2번)."""
    finding = {
        "issue_type": "bug",
        "file": "app/chain.py",
        "line": 12,
        "severity": "high",
        "message": (
            "LLMChain은 제거되었습니다. 필요하면 langchain_classic에서 import하세요."
        ),
        "evidence": [{"type": "code", "source": "app/chain.py:12"}],
        "suggestion": "호환성이 필요하면 langchain_classic 패키지를 설치하세요.",
    }
    models = _models([finding])
    # 재생성도 여전히 langchain_classic을 권함 → 결정적 대체
    models["bug_reviewer"].messages = iter(
        list(models["bug_reviewer"].messages)
        + [
            fake(
                call(
                    "SuggestionFix",
                    message="langchain_classic으로 옮기면 됩니다.",
                    suggestion="langchain_classic을 쓰세요.",
                )
            ).messages.__next__()
        ]
    )
    # 종합 노드도 두 번 모두 요약·추천에 langchain_classic을 넣음
    bad_summary = SUMMARY[:4] + ["필요시 langchain_classic에서 import할 수 있습니다."]
    bad = call(
        "ReviewSummary",
        summary=bad_summary,
        recommendations=[
            "langchain_classic 설치 후 호환성 모드로 전환하세요.",
            "LCEL로 바꾸세요.",
        ],
    )
    models["synthesizer"] = fake(bad, bad)
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    assert "classic" not in out["answer"].lower()
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert review["findings"][0]["message"] == "LLMChain은 제거되었습니다."
    assert review["recommendations"] == ["LCEL로 바꾸세요."]
    assert len(review["summary"]) == 5


def test_summary_keeps_five_lines_when_whole_lines_are_removed(retriever):
    """요약의 한 줄 전체가 langchain_classic 권장이면 지우지 않고 중립 문장으로 바꾼다.

    재생성(1회)에서도 같은 요약이 나오면 결정적 대체로 넘어가며, 요약은 정확히 5줄이다.
    """
    from src.agent import FALLBACK_SUMMARY_LINE

    whole = "호환성이 필요하면 langchain_classic에서 import하세요."
    bad_summary = ["요약 1", "요약 2", whole, "요약 4", whole]
    bad = call(
        "ReviewSummary", summary=bad_summary, recommendations=["LCEL로 바꾸세요."]
    )
    models = _models([])
    models["synthesizer"] = fake(bad, bad)  # 첫 생성 + 재생성 모두 같은 문제
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert len(review["summary"]) == 5
    assert review["summary"] == [
        "요약 1",
        "요약 2",
        FALLBACK_SUMMARY_LINE,
        "요약 4",
        FALLBACK_SUMMARY_LINE,
    ]
    assert "classic" not in out["answer"].lower()
    stages = [e for e in out["trace"] if e.get("stage") == "classic"]
    assert len(stages) == 3  # 첫 생성·재생성 감지 2회 + 결정적 대체 1회
    assert stages[-1].get("fallback") is True
    answer_summary = out["answer"].split("## 요약\n", 1)[1].split("\n\n", 1)[0]
    assert len(answer_summary.splitlines()) == 5  # 화면에서도 5줄


def test_c2_removes_pep8_finding_on_normal_line(retriever):
    """C2: ruff 위반이 없는 정상 줄에 PEP8-maximum-line-length를 단 style 지적 제거.

    21번 diff(함정 케이스)의 12행은 정확히 88자라 E501이 아니다.
    제거 사유는 trace에 남고, 같은 diff의 다른 지적(코드 근거만 단 bug)은 그대로 둔다.
    """
    pep8 = {
        "issue_type": "style",
        "file": "app/limits.py",
        "line": 12,
        "severity": "low",
        "message": "줄이 너무 깁니다.",
        "evidence": [
            {"type": "doc", "source": "pep8", "rule_id": "PEP8-maximum-line-length"},
            {"type": "code", "source": "app/limits.py:12"},
        ],
        "suggestion": "줄을 나누세요.",
    }
    bug = {
        "issue_type": "bug",
        "file": "app/limits.py",
        "line": 19,
        "severity": "low",
        "message": "attempt가 음수면 0으로 취급됩니다.",
        "evidence": [{"type": "code", "source": "app/limits.py:19"}],
        "suggestion": "음수 입력을 검사하세요.",
    }
    models = _models([bug])
    models["style_reviewer"] = fake(
        call("search_evidence", query="PEP8-maximum-line-length line too long E501"),
        call("ReviewerFindings", findings=[pep8]),
    )
    out = run_query(
        "data/eval_diffs/21_trap_long_lines.diff 를 리뷰해줘",
        llm=models,
        retriever=retriever,
    )
    ev = next(e for e in out["trace"] if e.get("stage") == "output")
    # 출력 검증 전 지적은 그대로 기록 (근거-지적 일치율 계산용)
    raw = {(f["file"], f["line"]): f for f in ev["raw_findings"]}
    assert "PEP8-maximum-line-length" in raw[("app/limits.py", 12)]["rule_ids"]
    # C2가 제거하고 사유를 남김
    assert [(r["file"], r["line"]) for r in ev["c2_removed"]] == [("app/limits.py", 12)]
    assert "ruff 위반" in ev["c2_removed"][0]["reason"]
    # 근거 확인 비율(존재)에는 C2 제거분을 섞지 않음: 존재 검사 통과 2건, C2 후 1건
    assert (ev["before"], ev["after"], ev["after_c2"]) == (2, 2, 1)
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert [(f["file"], f["line"]) for f in review["findings"]] == [
        ("app/limits.py", 19)
    ]
