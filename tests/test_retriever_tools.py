"""src/retriever.py·src/tools.py 테스트 (TASKS A3).

가짜 임베딩(DeterministicFakeEmbedding)과 임시 Chroma 폴더를 써서
실제 API를 호출하지 않습니다.
평가 diff 03·04·10은 교육용 더미 데이터입니다.
"""

import json

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding

from src import config
from src import diff_parser as dp
from src.retriever import build_retriever, load_corpus
from src.tools import ToolLog, build_tools

PEP8_IDS = {
    "PEP8-maximum-line-length",
    "PEP8-imports",
    "PEP8-whitespace-in-expressions-and-statements",
    "PEP8-naming-conventions",
    "PEP8-programming-recommendations",
}


def _diff(name: str) -> list[dp.FileDiff]:
    text = (config.EVAL_DIFFS_DIR / f"{name}.diff").read_text(encoding="utf-8")
    return dp.parse_diff(text)


@pytest.fixture(scope="module")
def retriever(tmp_path_factory):
    persist = tmp_path_factory.mktemp("chroma")
    return build_retriever(
        embeddings=DeterministicFakeEmbedding(size=32),
        persist_directory=str(persist),
        collection_name="test_corpus",
    )


def test_corpus_chunks_have_metadata():
    """코퍼스 = PEP 8 5개 섹션 + deprecated 7행, 모든 청크에 메타데이터 4종."""
    docs = load_corpus()
    ids = {d.metadata["rule_id"] for d in docs}
    assert PEP8_IDS <= ids
    assert {i for i in ids if i.startswith("DEP-")} == {
        "DEP-create_react_agent",
        "DEP-LLMChain",
        "DEP-initialize_agent",
        "DEP-AgentExecutor",
        "DEP-memory",
        "DEP-retrievers",
        "DEP-langchain_classic",
    }
    assert len(docs) == 12
    for d in docs:
        for key in ("source", "rule_id", "title", "language"):
            assert d.metadata[key], (d.metadata, key)


def test_bm25_ranks_exact_api_name_first(retriever):
    """키워드 검색은 정확한 API 이름 질의에서 해당 청크를 1위로 낸다.

    하이브리드 결과 전체는 가짜 임베딩(무작위 벡터)으로는 의미가 없어서,
    실제 Titan 임베딩 확인은 TASKS A3 확인 단계에서 따로 합니다.
    """
    retriever.bm25.k = 5
    hits = retriever.bm25.invoke("from langchain.chains import LLMChain")
    assert hits[0].metadata["rule_id"] == "DEP-LLMChain"


def test_rrf_merge_combines_rankings():
    """RRF: 두 목록에 모두 나온 청크가 한쪽 1위보다 앞설 수 있다."""
    from langchain_core.documents import Document

    from src.retriever import rrf_merge

    def doc(rid):
        return Document(page_content=rid, metadata={"rule_id": rid})

    first = [doc("A"), doc("B"), doc("C")]
    second = [doc("B"), doc("C"), doc("A")]
    merged = rrf_merge([first, second], k=2)
    assert [d.metadata["rule_id"] for d in merged] == ["B", "A"]


def test_ruff_check_reports_only_changed_line_violations(retriever):
    """04는 9행 E501(PEP 8 힌트 포함), 03·10은 위반 없음."""
    log = ToolLog()
    ruff_check, _ = build_tools(_diff("04_long_line"), retriever, log)
    result = json.loads(ruff_check.invoke({"file": "app/prompts.py"}))
    assert [(r["line"], r["code"], r["pep8_rule_id"]) for r in result] == [
        (9, "E501", "PEP8-maximum-line-length")
    ]
    assert log.calls == [{"name": "ruff_check", "args": {"file": "app/prompts.py"}}]
    for name in ("03_llmchain", "10_clean_refactor"):
        ruff_check, _ = build_tools(_diff(name), retriever, ToolLog())
        assert ruff_check.invoke({"file": ""}) == "위반 없음"


def test_search_evidence_records_retrieved_chunks(retriever):
    log = ToolLog()
    _, search_evidence = build_tools(_diff("03_llmchain"), retriever, log)
    result = json.loads(search_evidence.invoke({"query": "LLMChain langchain.chains"}))
    assert len(result) == 5 and all("rule_id" in r for r in result)
    assert set(log.retrieved) == {r["rule_id"] for r in result}
    assert log.calls == [
        {"name": "search_evidence", "args": {"query": "LLMChain langchain.chains"}}
    ]
