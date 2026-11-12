"""하이브리드 검색: BM25(키워드) + Chroma 벡터 검색을 RRF로 합칩니다.

`EnsembleRetriever`는 1.x에서 langchain_classic으로 옮겨져 쓰지 않습니다(CLAUDE.md 1번).
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.documents import Document

from src.evidence.corpus import load_corpus, tokenize
from src.shared import config

RRF_K = 60  # RRF 상수 (일반적인 기본값)


@dataclass
class HybridRetriever:
    """BM25 + Chroma 벡터 검색을 RRF로 합치는 검색기."""

    docs: list[Document]
    bm25: object  # langchain_community BM25Retriever
    vectorstore: object  # langchain_chroma Chroma
    k: int = 5

    def search(self, query: str, k: int | None = None) -> list[Document]:
        """질의에 맞는 청크 상위 k개를 돌려줍니다 (rule_id 기준 중복 제거)."""
        k = k or self.k
        # 코퍼스가 작으므로(12청크) 두 검색기에서 전체 순위를 받아 RRF로 합칩니다.
        # 한쪽에만 나온 청크가 순위 밖으로 밀려 점수를 잃는 일을 막기 위해서입니다.
        depth = len(self.docs)
        self.bm25.k = depth
        keyword_hits = self.bm25.invoke(query)
        vector_hits = self.vectorstore.similarity_search(query, k=depth)
        return rrf_merge([keyword_hits, vector_hits], k=k)


def rrf_merge(
    rankings: list[list[Document]], k: int, rrf_k: int = RRF_K
) -> list[Document]:
    """여러 검색 결과 순위를 Reciprocal Rank Fusion으로 합칩니다.

    점수 = Σ 1 / (rrf_k + 순위). 같은 rule_id는 한 청크로 봅니다.
    """
    scores: dict[str, float] = {}
    first_doc: dict[str, Document] = {}
    for ranking in rankings:
        for rank, doc in enumerate(ranking, start=1):
            rid = doc.metadata["rule_id"]
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (rrf_k + rank)
            first_doc.setdefault(rid, doc)
    ordered = sorted(scores, key=lambda rid: scores[rid], reverse=True)
    return [first_doc[rid] for rid in ordered[:k]]


def build_retriever(
    embeddings=None,
    persist_directory: str | None = None,
    collection_name: str = config.CHROMA_COLLECTION,
    k: int = 5,
) -> HybridRetriever:
    """코퍼스를 색인하고 하이브리드 검색기를 만듭니다.

    Chroma 컬렉션의 청크 id(rule_id)와 내용이 코퍼스와 같으면
    다시 임베딩하지 않고 재사용합니다.
    코퍼스가 바뀌었으면 컬렉션을 비우고 다시 색인합니다.
    """
    from langchain_chroma import Chroma
    from langchain_community.retrievers import BM25Retriever

    docs = load_corpus()
    embeddings = embeddings if embeddings is not None else config.get_embeddings()
    persist = persist_directory or str(config.ROOT_DIR / config.CHROMA_PERSIST_DIR)
    store = Chroma(
        collection_name=collection_name,
        embedding_function=embeddings,
        persist_directory=persist,
    )
    ids = [d.metadata["rule_id"] for d in docs]
    existing = store.get(include=["documents"])
    same = sorted(existing["ids"]) == sorted(ids) and sorted(
        existing["documents"]
    ) == sorted(d.page_content for d in docs)
    if not same:
        if existing["ids"]:
            store.delete(ids=existing["ids"])
        store.add_documents(docs, ids=ids)
    bm25 = BM25Retriever.from_documents(docs, preprocess_func=tokenize, k=k)
    return HybridRetriever(docs=docs, bm25=bm25, vectorstore=store, k=k)
