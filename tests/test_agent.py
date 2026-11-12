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

# 종합 노드(LLM)는 요약 1줄(change)·5줄(action)·추천 개선안만 씀 (2·3·4줄은 코드)
CHANGE = "app/chain.py에 LLMChain 기반 요약 체인을 추가합니다."
ACTION = "먼저 app/chain.py의 제거된 API import를 LCEL로 바꾸세요."
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
        "synthesizer": fake(draft()),
    }


def draft(change=CHANGE, action=ACTION, recommendations=("구버전 import 정리",)):
    """종합 노드의 가짜 구조화 출력 (SummaryDraft)."""
    return call(
        "SummaryDraft",
        change=change,
        action=action,
        recommendations=list(recommendations),
    )


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
    # 1·5줄은 LLM 문장 그대로, 2·3·4줄은 코드가 지적 목록과 설정에서 만듦
    assert review["summary"][0] == CHANGE and review["summary"][4] == ACTION
    assert review["summary"][1].startswith("보안 지적 0건, 버그 지적 1건")
    assert "app/chain.py 12행" in review["summary"][1]
    assert review["summary"][3].startswith("리뷰 범위: app/chain.py")
    assert config.DEFAULT_SETTINGS.label() in review["summary"][3]
    summary_ev = next(e for e in out["trace"] if e.get("stage") == "summary")
    assert summary_ev["replaced"] == []
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
    # 종합 노드도 두 번 모두 할 일(action)·추천에 langchain_classic을 넣음
    bad = draft(
        action="필요시 langchain_classic에서 import하세요.",
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
    assert review["summary"][4] == "먼저 app/chain.py 12행의 버그 지적을 고치세요."


def test_llm_summary_lines_are_replaced_after_one_retry(retriever):
    """LLM 줄(change·action)이 재생성 뒤에도 규칙을 어기면 그 줄만 코드 문장으로 바꾼다.

    change: 추측·평가 표현 → 파일별 추가/삭제 줄 수 문장.
    action: 지적 목록에 없는 줄 번호 → 가장 심각한 꼭 고칠 것 문장 (지적 없으면 "없음").
    """
    from src.agent import action_fallback, change_fallback
    from src.guardrails import check_input

    bad = draft(
        change="전반적으로 양호한 변경으로 보입니다.",
        action="999번 줄을 먼저 고치세요.",
        recommendations=["LCEL로 바꾸세요."],
    )
    models = _models([])
    models["synthesizer"] = fake(bad, bad)  # 첫 생성 + 재생성 모두 같은 문제
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    text = (config.EVAL_DIFFS_DIR / "03_llmchain.diff").read_text(encoding="utf-8")
    assert review["summary"][0] == change_fallback(check_input(text).py_files)
    assert review["summary"][4] == action_fallback([]) == "꼭 고쳐야 할 것은 없습니다."
    assert review["recommendations"] == ["LCEL로 바꾸세요."]
    rules = [e for e in out["trace"] if e.get("stage") == "summary_rules"]
    assert len(rules) == 2  # 첫 생성·재생성 모두 위반 감지
    final = next(e for e in out["trace"] if e.get("stage") == "summary")
    assert final["replaced"] == ["change", "action"] and final["attempts"] == 2
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


def test_synth_payload_orders_groups_and_drops_notes():
    """요약 입력: security·bug 먼저, 인젝션 참고 제외, 파일별 E501은 한 건으로 묶음."""
    from src.agent import synth_payload
    from src.schema import Finding

    def f(issue_type, line, rule_id, severity="low"):
        return Finding(
            issue_type=issue_type,
            file="a.py",
            line=line,
            severity=severity,
            message="m",
            evidence=[{"type": "tool", "source": "x", "rule_id": rule_id}],
            suggestion="s",
        )

    payload = synth_payload(
        [
            f("style", 5, "ruff:E501"),
            f("style", 9, "ruff:E501"),
            f("security", 2, "injection-scanner:ignore-instructions"),  # 참고(low)
            f("bug", 7, "DEP-LLMChain", "high"),
        ]
    )
    assert [p["issue_type"] for p in payload] == ["bug", "style"]
    assert payload[1]["message"] == "줄 길이 초과: 긴 줄 2개 (줄 번호 5, 9)"


def _finding(issue_type, line, rule_id, severity="low", file="a.py"):
    from src.schema import Finding

    return Finding(
        issue_type=issue_type,
        file=file,
        line=line,
        severity=severity,
        message="m",
        evidence=[{"type": "tool", "source": "x", "rule_id": rule_id}],
        suggestion="s",
    )


def test_code_summary_lines_and_llm_line_rules():
    """요약 2·3·4줄은 코드가 만들고, LLM 줄(1·5)은 코드 규칙으로 검사한다."""
    from src.agent import (
        action_violation,
        change_violation,
        scope_line,
        secbug_line,
        style_line,
    )

    findings = [
        _finding("style", 5, "ruff:E501"),
        _finding("style", 9, "ruff:E501"),
        _finding("style", 3, "ruff:N802"),
        _finding("security", 2, "injection-scanner:ignore-instructions"),  # 참고
        _finding("bug", 7, "DEP-LLMChain", "high"),
    ]
    # 인젝션 참고(low)는 세지 않고, 꼭 고칠 것 중 첫 위치를 알려 줌
    assert secbug_line(findings) == (
        "보안 지적 0건, 버그 지적 1건이며 꼭 고칠 것은 1건입니다 "
        "(먼저 볼 곳: a.py 7행)."
    )
    assert secbug_line(findings[:3]) == "보안·버그 지적은 없습니다."
    # 스타일은 항상 한 줄: E501은 줄 수로 묶고 나머지는 ruff 코드
    assert style_line(findings) == (
        "스타일: 줄 길이 초과 2줄, 그 밖의 스타일 1건 (N802)."
    )
    assert style_line([]) == "스타일 지적은 없습니다."
    scope = scope_line(["a.py"], ["README.md"], config.DEFAULT_SETTINGS)
    assert scope.startswith("리뷰 범위: a.py; 건너뜀: README.md; 기준: 줄 길이 88칸")
    # LLM 줄 규칙
    assert change_violation("a.py에 함수 f를 추가합니다.") is None
    assert change_violation("깔끔하게 잘 작성된 코드입니다.") == "추측·평가성 표현"
    classic = change_violation("langchain_classic으로 옮깁니다.")
    assert classic == "langchain_classic 권장"
    assert action_violation("7번 줄의 import를 고치세요.", findings) is None
    unknown = action_violation("8번 줄을 고치세요.", findings)
    assert unknown == "지적 목록에 없는 줄 번호"
    assert action_violation("꼭 고쳐야 할 것은 없습니다.", []) is None


def test_review_settings_priority_and_cap(tmp_path):
    """변경 한도: 사용자 지정 > 저장소 [tool.citereview] > 기본 300, 최대 500."""
    from src.config import MAX_LINES_CAP, load_review_settings

    assert load_review_settings().max_lines == 300  # 아무것도 없으면 기본값
    assert load_review_settings(None, 450).max_lines_source == "사용자 지정"
    with pytest.raises(ValueError):
        load_review_settings(None, MAX_LINES_CAP + 1)  # CLI·GUI 지정은 500 초과 거부
    with pytest.raises(ValueError):
        load_review_settings(None, 0)

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[tool.citereview]\nmax-lines = 400\n", encoding="utf-8"
    )
    s = load_review_settings(repo)
    assert (s.max_lines, s.max_lines_source) == (400, "저장소 설정")
    assert load_review_settings(repo, 350).max_lines == 350  # 사용자 지정이 우선
    # 저장소 값이 500을 넘으면 500으로 자르고 그 사실을 표시
    (repo / "pyproject.toml").write_text(
        "[tool.citereview]\nmax-lines = 900\n", encoding="utf-8"
    )
    s = load_review_settings(repo)
    assert s.max_lines == 500 and "900→500" in s.max_lines_source
    # ruff 설정이 없는 저장소는 CiteReview ruff.toml 그대로
    assert s.ruff_config == config.RUFF_CONFIG_PATH


def test_check_input_uses_max_lines():
    """300줄 초과 diff는 기본값에서 거절, 한도 500이면 통과 (같은 diff)."""
    from src.guardrails import check_input

    body = "".join(f"+X_{i} = {i}\n" for i in range(350))
    diff = (
        "diff --git a/app/big.py b/app/big.py\n--- /dev/null\n+++ b/app/big.py\n"
        f"@@ -0,0 +1,350 @@\n{body}"
    )
    assert check_input(diff).status == "rejected"
    assert check_input(diff, 500).status != "rejected"


def test_run_ruff_follows_repo_ruff_setting(tmp_path):
    """저장소 ruff 설정이 있으면 그 줄 길이 기준으로 검사한다 (04번 diff 9행 = 95칸)."""
    from src import diff_parser as dp
    from src.config import load_review_settings
    from src.tools import run_ruff

    text = (config.EVAL_DIFFS_DIR / "04_long_line.diff").read_text(encoding="utf-8")
    (file,) = dp.split_python(dp.parse_diff(text))[0]

    def e501(settings=None):
        return [
            (r["line"], r["message"])
            for r in run_ruff(file, settings)
            if r["code"] == "E501"
        ]

    assert [(ln, "(95 > 88)" in m) for ln, m in e501()] == [(9, True)]  # 기본값

    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "ruff.toml").write_text(
        'line-length = 120\n[lint]\nselect = ["E"]\n', encoding="utf-8"
    )
    s = load_review_settings(loose)
    assert (s.line_length, s.ruff_source) == (120, "저장소 ruff.toml")
    assert e501(s) == []  # 120칸 기준에서는 위반 아님

    strict = tmp_path / "strict"
    strict.mkdir()
    (strict / "pyproject.toml").write_text(
        '[tool.ruff]\nline-length = 60\n[tool.ruff.lint]\nselect = ["E"]\n',
        encoding="utf-8",
    )
    s = load_review_settings(strict)
    assert s.ruff_source == "저장소 pyproject.toml [tool.ruff]"
    rows = e501(s)
    assert 9 in [ln for ln, _ in rows] and all("> 60)" in m for _, m in rows)


def test_line_length_findings_are_made_by_code(retriever):
    """줄 길이 지적은 ruff 결과로 코드가 만들고, 리뷰어가 낸 E501은 버린다."""
    reviewer_e501 = {
        "issue_type": "style",
        "file": "app/prompts.py",
        "line": 9,
        "severity": "low",
        "message": "줄이 120자로 너무 깁니다.",  # 리뷰어가 잘못 센 숫자
        "evidence": [{"type": "tool", "source": "ruff", "rule_id": "ruff:E501"}],
        "suggestion": "줄을 나누세요.",
    }
    models = _models([], [reviewer_e501])
    out = run_query(
        "data/eval_diffs/04_long_line.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    ev = next(e for e in out["trace"] if e.get("stage") == "output")
    assert ev["line_length_by_code"] == 1 and ev["reviewer_line_length_dropped"] == 1
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    (f,) = [f for f in review["findings"] if f["line"] == 9]
    assert "95칸" in f["message"] and "120" not in f["message"]  # 숫자는 ruff 값
    rule_ids = [e["rule_id"] for e in f["evidence"]]
    assert rule_ids == ["ruff:E501", "PEP8-maximum-line-length"]
    assert "PEP8-maximum-line-length" in {c["rule_id"] for c in out["contexts"]}
    assert review["summary"][2] == "스타일: 줄 길이 초과 1줄."


def test_reviewer_without_result_tool_is_finalized(retriever):
    """리뷰어가 결과 도구 없이 글로 끝나면 보완 단계가 대화 기록으로 한 번 더 묻는다.

    gpt-oss는 tool_choice="any"를 강제하지 않아 실제로 이런 경우가 생김.
    """
    finding = {
        "issue_type": "bug",
        "file": "app/chain.py",
        "line": 12,
        "severity": "high",
        "message": "langchain.chains는 1.x에서 제거되어 ImportError가 납니다.",
        "evidence": [{"type": "code", "source": "app/chain.py:12"}],
        "suggestion": "LCEL로 바꿉니다.",
    }
    models = _models([])
    models["bug_reviewer"] = fake(
        "리뷰를 마쳤습니다.",  # 결과 도구를 부르지 않고 끝남
        call("ReviewerFindings", findings=[finding]),  # 보완 단계의 답
    )
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    reviewers = {e["name"]: e for e in out["trace"] if e["type"] == "reviewer"}
    assert reviewers["bug_reviewer"]["structured"] == "finalize"
    assert reviewers["style_reviewer"]["structured"] == "agent"
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert [f["line"] for f in review["findings"]] == [12]


def test_missing_reviewer_result_is_shown_as_unchecked_not_no_issues(retriever):
    """스타일 리뷰어가 결과 도구도 보완 단계도 실패하면(none) "문제 없음"과 구분해 표시.

    맨 위 안내 줄에 "결과 없음(확인 못 함)", 스타일 섹션은 "(없음)" 대신 확인 못 함,
    "문제를 찾지 못했습니다."는 쓰지 않는다. trace에 리뷰어별 상태를 남긴다.
    """
    models = _models([])
    models["style_reviewer"] = fake(
        "리뷰를 마쳤습니다.",  # 결과 도구 없이 끝남
        "스타일 문제는 없습니다.",  # 보완 단계도 글로만 답함 → none
    )
    text = (config.EVAL_DIFFS_DIR / "10_clean_refactor.diff").read_text("utf-8")
    assert text  # 지적이 하나도 없는 diff (버그 리뷰어도 0건)
    out = run_query(
        "data/eval_diffs/10_clean_refactor.diff 를 리뷰해줘",
        llm=models,
        retriever=retriever,
    )
    status = next(e for e in out["trace"] if e.get("stage") == "reviewers")
    assert status["structured"] == {"bug_reviewer": "agent", "style_reviewer": "none"}
    assert status["none"] == ["style_reviewer"]
    head = out["answer"].split("## 요약", 1)[0]
    assert "주의: 스타일 리뷰어 결과 없음(확인 못 함)" in head
    assert "문제를 찾지 못했습니다." not in out["answer"]
    style = out["answer"].split("## 스타일 이슈", 1)[1]
    assert style.lstrip().startswith("(확인 못 함: 스타일 리뷰어 결과 없음)")
    bug = out["answer"].split("### 버그", 1)[1].split("##", 1)[0]
    assert "(없음)" in bug  # 결과를 받은 버그 리뷰어 쪽은 그대로 "(없음)"


# ── 출력 검증 ④: 파싱 실패 주장 검증 (evaluation/notes/case10) ──────────────


def _utils_finding(line, message, severity="high"):
    return {
        "issue_type": "bug",
        "file": "app/utils.py",
        "line": line,
        "severity": severity,
        "message": message,
        "evidence": [{"type": "code", "source": f"app/utils.py:{line}"}],
        "suggestion": "들여쓰기를 맞춥니다.",
    }


# 10번 diff에서 gpt-oss가 실제로 낸 오탐과 같은 종류 (파일은 ast.parse로 파싱됨)
PARSE_CLAIM = _utils_finding(
    18, "함수 본문의 들여쓰기가 일관되지 않아 IndentationError가 발생합니다."
)
# 들여쓰기를 언급하지만 파싱 실패가 아니라 논리 버그를 주장하는 지적 → 유지돼야 함
LOGIC_BUG = _utils_finding(
    19,
    "들여쓰기 때문에 return이 루프 안에 있어 첫 태그만 처리하고 끝납니다.",
    "medium",
)


def test_parse_failure_claim_dropped_but_logic_bug_kept(retriever):
    """파일이 파싱되면 파싱 실패 주장만 제거하고, 들여쓰기 관련 논리 버그는 유지."""
    out = run_query(
        "data/eval_diffs/10_clean_refactor.diff 를 리뷰해줘",
        llm=_models([PARSE_CLAIM, LOGIC_BUG]),
        retriever=retriever,
    )
    ev = next(e for e in out["trace"] if e.get("stage") == "output")
    assert [(d["line"], d["severity"]) for d in ev["syntax_dropped"]] == [(18, "high")]
    assert "ast.parse" in ev["syntax_dropped"][0]["reason"]
    assert ev["syntax_dropped"][0]["finding"]["message"] == PARSE_CLAIM["message"]
    assert ev["syntax_kept"] == []
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert [f["line"] for f in review["findings"]] == [19]


def test_parse_failure_claim_kept_when_broken_or_partial():
    """실제로 파싱이 안 되거나 파일 일부만 보이면 지적을 유지하고 사유를 남긴다."""
    from src.guardrails import check_input, verify_syntax_claims
    from src.schema import Finding

    broken = check_input(
        "diff --git a/app/b.py b/app/b.py\n--- /dev/null\n+++ b/app/b.py\n"
        "@@ -0,0 +1,3 @@\n+def f():\n+    x = 1\n+      return x\n"
    )
    partial = check_input(
        "diff --git a/app/p.py b/app/p.py\n--- a/app/p.py\n+++ b/app/p.py\n"
        "@@ -10,2 +10,3 @@\n     y = 2\n+    z = 3\n     return y\n"
    )

    def claim(path, line):
        return Finding(
            issue_type="bug",
            file=path,
            line=line,
            severity="high",
            message="SyntaxError: 들여쓰기가 맞지 않습니다.",
            evidence=[{"type": "code", "source": f"{path}:{line}"}],
            suggestion="s",
        )

    kept, dropped, records = verify_syntax_claims(
        [claim("app/b.py", 3), claim("app/p.py", 11)],
        broken.py_files + partial.py_files,
    )
    assert len(kept) == 2 and dropped == []
    reasons = {r["file"]: r["reason"] for r in records}
    assert reasons["app/b.py"].startswith("실제 파싱 실패")
    assert reasons["app/p.py"] == "파일 일부만 보여 파싱 검증 불가"


# ── 요약의 줄 길이 숫자 검사 ─────────────────────────────────────────────


def test_length_numbers_checked_only_in_line_length_sentences():
    """줄 길이를 언급한 문장의 숫자만 적용 기준·ruff 값과 비교한다."""
    from src.agent import action_violation, length_number_violation

    lengths = {88, 95}  # 기준 88칸, ruff가 잰 길이 95칸
    wrong = length_number_violation("9행 줄 길이를 79자 이하로 줄이세요.", lengths)
    assert wrong == "줄 길이 숫자 불일치 (79)"
    ok = length_number_violation("9행 줄 길이를 88칸 이하로 줄이세요.", lengths)
    assert ok is None
    assert length_number_violation("E501: 95 characters (기준 88칸).", lengths) is None
    # 줄 길이를 언급하지 않은 문장의 숫자는 검사하지 않음
    assert length_number_violation("함수 이름을 20자 이내로 바꾸세요.", lengths) is None
    # 저장소 기준이 120칸이면 120만 허용
    assert length_number_violation("줄 길이를 120칸에 맞추세요.", {120}) is None
    assert length_number_violation("줄 길이를 88칸에 맞추세요.", {120}) is not None
    # 할 일 줄에도 같은 검사 (지적 목록의 줄 번호는 따로 검사)
    findings = [_finding("style", 9, "ruff:E501")]
    text = "9번 줄의 줄 길이를 79자 이하로 줄이세요."
    assert action_violation(text, findings, lengths) == "줄 길이 숫자 불일치 (79)"


def test_wrong_length_number_in_summary_is_replaced(retriever):
    """04번: 할 일 줄의 "79자"는 재생성 뒤에도 틀리면 코드 문장으로 바뀐다.

    적용 기준은 요약 4줄에만 나오고 3줄(스타일)에는 넣지 않는다.
    """
    bad = draft(
        change="app/prompts.py에 리뷰·요약 프롬프트 템플릿을 추가합니다.",
        action="줄 길이 초과가 있는 9번 줄을 79자 이하로 줄이세요.",
    )
    models = _models([])
    models["synthesizer"] = fake(bad, bad)
    out = run_query(
        "data/eval_diffs/04_long_line.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    summary = review["summary"]
    assert "79" not in summary[4]
    assert summary[0].startswith("app/prompts.py에")  # 문제없는 줄은 그대로
    assert "88칸" in summary[3] and "88" not in summary[2]
    final = next(e for e in out["trace"] if e.get("stage") == "summary")
    assert final["replaced"] == ["action"]
    assert final["problems"]["action"] == "줄 길이 숫자 불일치 (79)"


# ── 역할별 토큰 기록 (trace usage 이벤트) ───────────────────────────────


def _with_usage(name, n_in, n_out, **args):
    """usage_metadata가 붙은 가짜 tool_call 답."""
    from langchain_core.messages import AIMessage

    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": f"u_{n_in}"}],
        usage_metadata={
            "input_tokens": n_in,
            "output_tokens": n_out,
            "total_tokens": n_in + n_out,
        },
    )


def test_usage_is_recorded_per_role_with_retry_and_finalize_separate(retriever):
    """보완 단계(finalize)와 재생성(synthesize:retry) 토큰은 따로 집계된다."""
    from tests.fakes import ToolFake

    models = _models([])
    models["bug_reviewer"] = fake(
        "리뷰를 마쳤습니다.",  # 결과 도구 없이 끝남 → 보완 단계
        call("ReviewerFindings", findings=[]),
    )
    no_issue = "꼭 고쳐야 할 것은 없습니다."
    models["synthesizer"] = ToolFake(
        messages=iter(
            [
                # 규칙 위반(평가성 표현) → 재생성
                _with_usage(
                    "SummaryDraft", 100, 10, change="양호합니다.", action=no_issue
                ),
                _with_usage("SummaryDraft", 120, 12, change=CHANGE, action=no_issue),
            ]
        )
    )
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    usage = next(e for e in out["trace"] if e["type"] == "usage")
    roles = usage["by_role"]
    assert roles["synthesize"] == {"calls": 1, "input_tokens": 100, "output_tokens": 10}
    assert roles["synthesize:retry"] == {
        "calls": 1,
        "input_tokens": 120,
        "output_tokens": 12,
    }
    assert roles["bug_reviewer"]["calls"] == 1
    assert roles["bug_reviewer:finalize"]["calls"] == 1
    assert roles["style_reviewer"]["calls"] == 2
    assert roles["supervisor"]["calls"] == 3
    assert "기타" not in roles  # 모든 호출에 역할 표시가 붙음
    assert usage["total"]["input_tokens"] == 220
    assert usage["total"]["calls"] == sum(r["calls"] for r in roles.values())


def test_eval_summaries_for_usage_structured_and_syntax_rescue(retriever):
    """평가 집계: 토큰 합계, structured 분포, ④ 제거 덕분에 통과한 케이스 판정."""
    import csv
    import importlib.util

    path = config.ROOT_DIR / "evaluation" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval_for_agent_test", path)
    run_eval = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_eval)

    rows = csv.DictReader(run_eval.CSV_PATH.open(encoding="utf-8"))
    case = next(r for r in rows if r["id"] == "10")
    diff_path = config.EVAL_DIFFS_DIR / "10_clean_refactor.diff"
    diff_text = diff_path.read_text(encoding="utf-8")
    out = run_query(case["input"], llm=_models([PARSE_CLAIM]), retriever=retriever)
    # high 오탐이 제거되어 "high 지적" 금지 항목이 통과 → 제거가 없었다면 실패
    assert run_eval.syntax_rescued(case, out, diff_text) is True
    summary = run_eval.syntax_summary([(case, out, {"passed": True})])
    assert summary == {
        "dropped": 1,
        "kept": 0,
        "passed_because_dropped": 1,
        "passed_because_dropped_ids": ["10"],
    }
    assert run_eval.structured_summary([out["trace"]]) == {
        "total": {"agent": 2},
        "by_reviewer": {"bug_reviewer": {"agent": 1}, "style_reviewer": {"agent": 1}},
        "none_by_reviewer": {"bug_reviewer": 0, "style_reviewer": 0},
        "cases_with_none": 0,
    }
    usage = run_eval.usage_summary([out["trace"]])
    assert usage["cases_with_llm"] == 1 and usage["total"]["calls"] > 0


def test_run_eval_requires_review_model(monkeypatch):
    """평가 실행은 --review-model 없이 시작하지 않는다 (모델 이름 빠진 기록 방지)."""
    import importlib.util

    path = config.ROOT_DIR / "evaluation" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval_for_option_test", path)
    run_eval = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_eval)
    started = []
    monkeypatch.setattr(run_eval, "run_case", lambda *a, **k: started.append(a))
    with pytest.raises(SystemExit):
        run_eval.main(["--cases", "3", "--no-judge"])
    assert started == []


def test_verbose_shows_usage_totals():
    """--verbose에 역할별 토큰과 합계가 보인다."""
    from src.render import render_verbose

    usage = {
        "type": "usage",
        "t": "",
        "elapsed_ms": 0,
        "by_role": {
            "synthesize": {"calls": 1, "input_tokens": 1200, "output_tokens": 30}
        },
        "total": {"calls": 1, "input_tokens": 1200, "output_tokens": 30},
    }
    text = render_verbose([usage])
    assert "합계: 호출 1회 · 입력 1,200 · 출력 30" in text
    assert "- synthesize: 호출 1회" in text


# ── round3 이후 ②: 결과를 JSON 글로 답한 리뷰어 (평가 21번·day03·day04) ─────────


def _llmchain_finding():
    return {
        "issue_type": "bug",
        "file": "app/chain.py",
        "line": 12,
        "severity": "high",
        "message": "langchain.chains는 1.x에서 제거되어 ImportError가 납니다.",
        "evidence": [{"type": "code", "source": "app/chain.py:12"}],
        "suggestion": "LCEL로 바꿉니다.",
    }


def test_reviewer_json_text_is_parsed_without_extra_call(retriever):
    """리뷰어가 결과 도구 대신 JSON 글로 답하면 그 글을 파싱한다 (보완 호출 없음)."""
    models = _models([])
    models["bug_reviewer"] = fake(
        json.dumps({"findings": [_llmchain_finding()]}, ensure_ascii=False)
    )
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    reviewers = {e["name"]: e for e in out["trace"] if e["type"] == "reviewer"}
    assert reviewers["bug_reviewer"]["structured"] == "agent_text"
    usage = next(e for e in out["trace"] if e["type"] == "usage")
    assert "bug_reviewer:finalize" not in usage["by_role"]  # 보완 단계를 부르지 않음
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert [f["line"] for f in review["findings"]] == [12]
    assert "결과 없음" not in out["answer"]


def test_finalize_json_text_is_parsed_and_only_garbage_is_none(retriever):
    """보완 단계도 도구 없이 JSON 글(```json 울타리)로 답하면 파싱, 파싱 실패만 none."""
    from src.reviewers import parse_findings_text

    models = _models([])
    models["bug_reviewer"] = fake(
        "리뷰를 마쳤습니다.",  # JSON 아님 → 보완 단계
        "```json\n" + json.dumps({"findings": [_llmchain_finding()]}) + "\n```",
    )
    out = run_query(
        "data/eval_diffs/03_llmchain.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    reviewers = {e["name"]: e for e in out["trace"] if e["type"] == "reviewer"}
    assert reviewers["bug_reviewer"]["structured"] == "finalize_text"
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert [f["line"] for f in review["findings"]] == [12]
    # 파서 단위: 빈 목록·목록만 온 경우는 받고, 글·스키마 위반은 None
    assert parse_findings_text('{\n  "findings": []\n}').findings == []
    assert len(parse_findings_text(json.dumps([_llmchain_finding()])).findings) == 1
    assert parse_findings_text("스타일 문제는 없습니다.") is None
    assert parse_findings_text('{"findings": [{"line": "x"}]}') is None


# ── round3 이후 ③: 줄 길이는 화면 끝 "## 참고" ───────────────────────────


def test_line_length_recommendations_are_limited():
    """추천 개선안의 줄 길이 항목: 다른 지적이 있으면 빼고, 줄 길이만 있으면 1개만."""
    from src.agent import limit_line_length_recs

    e501 = [_finding("style", 9, "ruff:E501"), _finding("style", 12, "ruff:E501")]
    recs = [
        "긴 줄을 괄호 안에서 나누세요.",
        "줄 길이를 88칸 이하로 맞추세요.",
        "테스트 추가",
    ]
    assert limit_line_length_recs(recs, e501) == [recs[0], recs[2]]
    bug = _finding("bug", 7, "DEP-LLMChain", "high")
    assert limit_line_length_recs(recs, e501 + [bug]) == [recs[2]]
    # 인젝션 참고(low)는 "다른 지적"으로 치지 않음
    note = _finding("security", 2, "injection-scanner:ignore-instructions")
    assert limit_line_length_recs(recs, e501 + [note]) == [recs[0], recs[2]]


def test_post_body_keeps_line_length_note_and_verbose_has_detail(
    monkeypatch, retriever
):
    """04번: HITL 번호에 E501이 없고(번호를 빼도 영향 없음), 게시 본문에도 참고가 남음.

    줄별 길이·코드/주석 구분은 --verbose(trace stage="line_length")에서만 보인다.
    """
    from src.agent import resume_review
    from src.render import render_verbose

    monkeypatch.setenv("CITEREVIEW_ALLOWED_POST_REPOS", "me/practice-repo")
    models = _models([])
    models["synthesizer"] = fake(
        draft(
            change="app/prompts.py에 리뷰·요약 프롬프트 템플릿을 추가합니다.",
            action="꼭 고쳐야 할 것은 없습니다.",
            recommendations=[
                "긴 줄을 괄호 안에서 나누세요.",
                "줄 길이를 88칸에 맞추세요.",
            ],
        )
    )
    out = run_query(
        "data/eval_diffs/04_long_line.diff 를 리뷰해줘", llm=models, retriever=retriever
    )
    review = next(e["review"] for e in out["trace"] if e["type"] == "review")
    assert review["recommendations"] == ["긴 줄을 괄호 안에서 나누세요."]  # 최대 1개
    assert "## 참고\n줄 길이 초과 1줄 (기준 88칸): app/prompts.py 9" in out["answer"]
    assert "(95)" not in out["answer"]
    verbose = render_verbose(out["trace"])
    assert "## 줄 길이 상세 (--verbose)" in verbose and "9 (95)" in verbose
    tid = next(e["thread_id"] for e in out["trace"] if e["type"] == "hitl")
    posted = resume_review(
        tid, {"action": "approve", "ignore": [1], "repo": "me/practice-repo"}
    )
    assert posted["post"]["status"] == "posted"
    assert "줄 길이 초과 1줄 (기준 88칸): app/prompts.py 9" in posted["post"]["body"]


def test_default_review_model_is_sonnet_in_us_west_1():
    """기본 리뷰 모델은 Sonnet 4.6(us-west-1), 나머지 모델은 us-east-1 (호출 없음)."""
    assert config.REVIEW_MODEL_ID == "us.anthropic.claude-sonnet-4-6"
    assert config.review_model_region() == "us-west-1"
    assert config.review_model_region(config.REVIEW_MODELS["haiku"]) == "us-east-1"
    assert config.review_model_region(config.REVIEW_MODELS["gpt-oss"]) == "us-east-1"
    assert sorted(config.REVIEW_MODELS) == ["gpt-oss", "haiku", "sonnet"]


def test_regions_are_separated_by_role_and_match_ragas():
    """리전은 역할별 상수: 리뷰(모델별)·임베딩·판정. RAGAS 파일의 값과 같아야 함."""
    import re

    assert (config.EMBEDDING_REGION, config.JUDGE_REGION) == ("us-east-1", "us-east-1")
    ragas = (config.ROOT_DIR / "evaluation" / "run_ragas.py").read_text("utf-8")
    for name in ("JUDGE_REGION", "EMBEDDING_REGION"):
        (value,) = re.findall(rf'^{name} = "([^"]+)"', ragas, re.M)
        assert value == getattr(config, name)
