"""제출 규약 진입점: 메인 에이전트 그래프와 핵심 함수 `run_query`.

과정 제출 규약(src/agent.py 필수)과 `from src.agent import run_query` 경로를 지키려고
남겨 둔 재노출 파일입니다. 구현은 `src/review/graph.py`에 있습니다
(2026-11-12 도메인 구조 전환, CLAUDE.md 2번).
"""

from src.review.graph import build_graph, extract_diff_path, resume_review, run_query

__all__ = ["build_graph", "extract_diff_path", "resume_review", "run_query"]
