"""RAG 파이프라인: 코퍼스 청크 분할, Chroma 색인, 하이브리드 검색(BM25 + 벡터, RRF).

- 코퍼스는 두 파일입니다 (CLAUDE.md 2번):
  - `data/corpus/pep8.md`: `## <rule_id>` 제목 하나가 청크 하나
    (`- title:`·`- source:` 메타 줄)
  - `data/corpus/deprecated_map.md`: 표의 행 하나가 청크 하나 (첫 열이 rule_id)
- 모든 청크 메타데이터에 `source`·`rule_id`·`title`·`language="python"`을 넣습니다.
- 하이브리드 검색: BM25(키워드)와 Chroma(벡터) 결과를 **RRF로 직접 합칩니다.**
  `EnsembleRetriever`는 1.x에서 `langchain_classic`으로 옮겨져 쓰지 않습니다.
- 임베딩은 `embeddings` 인자로 주입할 수 있습니다. `None`이면 `config.get_embeddings()`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document

from src import config

PEP8_PATH = config.CORPUS_DIR / "pep8.md"
DEPRECATED_PATH = config.CORPUS_DIR / "deprecated_map.md"
RRF_K = 60  # RRF 상수 (일반적인 기본값)

# BM25 토큰: 영문 식별자(점·밑줄 포함 경로를 조각으로), 숫자, 한글 단어
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[가-힣]+")


def tokenize(text: str) -> list[str]:
    """BM25용 토큰 분리. `langchain.chains`·`LLMChain`·`E501` 같은 이름이 그대로 맞도록
    식별자 단위로 자르고 소문자로 바꿉니다."""
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _relative(path: Path) -> str:
    return path.relative_to(config.ROOT_DIR).as_posix()


def load_pep8(path: Path = PEP8_PATH) -> list[Document]:
    """pep8.md를 `## <rule_id>` 단위 청크로 나눕니다."""
    text = path.read_text(encoding="utf-8")
    docs: list[Document] = []
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        head, _, rest = block.partition("\n")
        rule_id = head.strip()
        title = re.search(r"^- title:\s*(.+)$", rest, re.MULTILINE)
        url = re.search(r"^- source:\s*(.+)$", rest, re.MULTILINE)
        body = re.sub(r"^- (title|source):.*$", "", rest, flags=re.MULTILINE)
        body = body.strip().removesuffix("---").strip()
        docs.append(
            Document(
                page_content=body,
                metadata={
                    "rule_id": rule_id,
                    "title": title.group(1).strip() if title else rule_id,
                    "source": _relative(path),
                    "url": url.group(1).strip() if url else "",
                    "language": "python",
                },
            )
        )
    return docs


def load_deprecated(path: Path = DEPRECATED_PATH) -> list[Document]:
    """deprecated_map.md 표를 행 단위 청크로 나눕니다."""
    rows = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `DEP-")
    ]
    header = ["규칙 ID", "구버전 사용", "1.x 상태", "대체", "분류", "출처"]
    docs: list[Document] = []
    for row in rows:
        # 셀 안의 `\|`(파이프 문자)는 구분자가 아니므로 잠시 바꿔 둠
        cells = [
            c.strip().replace("\x00", "|")
            for c in row.replace("\\|", "\x00").strip("|").split("|")
        ]
        rule_id = cells[0].strip("`")
        old_api = cells[1].strip("`")
        content = "\n".join(f"{h}: {c}" for h, c in zip(header, cells, strict=False))
        docs.append(
            Document(
                page_content=f"deprecated API 매핑 — {old_api}\n{content}",
                metadata={
                    "rule_id": rule_id,
                    "title": f"deprecated API 매핑표 — {rule_id.removeprefix('DEP-')}",
                    "source": _relative(path),
                    "url": cells[5] if cells[5].startswith("http") else "",
                    "language": "python",
                },
            )
        )
    return docs


def load_corpus() -> list[Document]:
    """코퍼스 전체 청크 (PEP 8 섹션 + deprecated 매핑표 행)."""
    return load_pep8() + load_deprecated()


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
