"""GitHub 연동: PR 코멘트 게시(dry-run)와 게시 허용 목록.

게시 대상은 `.env`의 허용 목록만으로 정합니다(LLM 출력·diff 내용은 읽지 않음).
"""

from __future__ import annotations

from src.shared.config import load_env
from src.toolkit.agent_tools import ToolLog


def allowed_post_repos() -> set[str]:
    """게시 허용 목록 (`.env`의 CITEREVIEW_ALLOWED_POST_REPOS, 쉼표 구분 owner/repo).

    게시 대상은 이 목록만으로 정합니다. LLM 출력이나 diff 내용은 읽지 않습니다
    (SERVICE.md 4번 정책 3·5의 "코드로 강제").
    """
    import os

    load_env()
    raw = os.environ.get("CITEREVIEW_ALLOWED_POST_REPOS", "")
    return {r.strip() for r in raw.split(",") if r.strip()}


def github_post_comment(repo: str, body: str, log: ToolLog) -> dict:
    """승인된 리뷰를 PR 코멘트로 게시합니다. **이번 범위는 dry-run**(내용만 돌려줌).

    LLM이 고르는 도구가 아니라 HITL 승인 뒤 게시 노드가 직접 부릅니다.
    허용 목록 확인은 호출하는 쪽(게시 노드)이 코드로 먼저 합니다.
    """
    log.record("github_post_comment", {"repo": repo, "dry_run": True})
    return {"repo": repo, "dry_run": True, "body": body}
