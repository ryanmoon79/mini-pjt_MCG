"""제출 규약 진입점: RAG 파이프라인(코퍼스 청크 분할, 색인, 하이브리드 검색).

과정 제출 규약(src/retriever.py 필수)과 `from src.retriever import build_retriever`
경로를 지키려고 남겨 둔 재노출 파일입니다. 구현은 `src/evidence/`에 있습니다
(corpus.py: 청크, search.py: 하이브리드 검색; 2026-11-12 도메인 구조 전환).
"""

from src.evidence.corpus import load_corpus, load_deprecated, load_pep8, tokenize
from src.evidence.search import HybridRetriever, build_retriever, rrf_merge

__all__ = [
    "HybridRetriever",
    "build_retriever",
    "load_corpus",
    "load_deprecated",
    "load_pep8",
    "rrf_merge",
    "tokenize",
]
