"""도메인 도구: 리뷰어 에이전트가 스스로 골라 쓰는 도구 (SERVICE.md 3번 확정 이름).

- `ruff_check`: 변경된 .py 파일에 ruff를 돌려, **변경된 줄**의 위반만 돌려줍니다.
- `search_evidence`: PEP 8·deprecated 매핑표 코퍼스를 하이브리드 검색합니다.
- `github_post_comment`(dry-run)·`github_get_pr`(선택)은 2부에서 덧붙입니다.

도구는 리뷰 한 건의 diff에 묶여야 하므로 `build_tools()`가 그때그때 만듭니다.
도구 호출과 출력은 `ToolLog`에 기록되어, 출력 검증(근거 실재 확인)과 `trace`가 씁니다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field

from langchain_core.tools import tool

from src import config
from src import diff_parser as dp

# ruff 코드 → PEP 8 근거 (evaluation/README.md 연결표와 같은 내용).
# ruff 위반을 지적할 때 search_evidence로 함께 찾아볼 문서 rule_id 힌트입니다.
RUFF_TO_PEP8 = {
    "E501": "PEP8-maximum-line-length",
    "E401": "PEP8-imports",
    "E402": "PEP8-imports",
    "I001": "PEP8-imports",
    "E711": "PEP8-programming-recommendations",
    "E712": "PEP8-programming-recommendations",
    "E721": "PEP8-programming-recommendations",
    "E722": "PEP8-programming-recommendations",
    "E731": "PEP8-programming-recommendations",
    "W291": "PEP8-whitespace-in-expressions-and-statements",
    "W293": "PEP8-whitespace-in-expressions-and-statements",
}


def _pep8_hint(code: str) -> str | None:
    if code.startswith("N8"):
        return "PEP8-naming-conventions"
    return RUFF_TO_PEP8.get(code)


@dataclass
class ToolLog:
    """리뷰 한 건 동안의 도구 호출 기록 (근거 실재 확인·trace용)."""

    calls: list[dict] = field(default_factory=list)  # {"name", "args"}
    ruff_findings: list[dict] = field(default_factory=list)  # ruff_check 출력 누적
    retrieved: dict[str, dict] = field(default_factory=dict)  # rule_id → 청크
    trace: object | None = None  # observability.TraceRecorder (있으면 도구 이벤트 기록)

    def record(self, name: str, args: dict) -> None:
        self.calls.append({"name": name, "args": args})
        if self.trace is not None:
            self.trace.add("tool", name=name, args=args)


def reconstruct_file(file: dp.FileDiff) -> tuple[str, bool]:
    """diff에서 변경 후 파일 내용을 다시 만듭니다.

    diff에 보이지 않는 줄은 빈 줄로 채워 줄 번호를 맞춥니다.
    돌려주는 두 번째 값은 "파일 전체가 diff 안에 보이는가"입니다
    (1행부터 끝까지 빈틈없이 보이면 True).
    """
    visible = dp.new_side_lines(file)
    if not visible:
        return "", True
    last = max(visible)
    complete = all(n in visible for n in range(1, last + 1))
    lines = [visible.get(n, "") for n in range(1, last + 1)]
    return "\n".join(lines) + "\n", complete


def run_ruff(file: dp.FileDiff) -> list[dict]:
    """파일 하나에 ruff를 돌려 **변경된 줄**의 위반만 돌려줍니다.

    파일 일부만 보이는 diff에서는 파일 전체를 봐야 하는 pyflakes(F)와
    import 정렬(I) 규칙이 가짜 위반을 내므로 제외합니다.
    """
    text, complete = reconstruct_file(file)
    if not text:
        return []
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--config",
        str(config.RUFF_CONFIG_PATH),
        "--output-format",
        "json",
        "--stdin-filename",
        file.path,
        "-",
    ]
    if not complete:
        cmd[4:4] = ["--extend-ignore", "F,I"]
    result = subprocess.run(
        cmd,
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=config.ROOT_DIR,
    )
    changed = dp.changed_lines(file)
    findings = []
    for v in json.loads(result.stdout or "[]"):
        line = v["location"]["row"]
        if line not in changed:
            continue
        code = v["code"]
        findings.append(
            {
                "file": file.path,
                "line": line,
                "code": code,
                "rule_id": f"ruff:{code}",
                "message": v["message"],
                "pep8_rule_id": _pep8_hint(code),
            }
        )
    return findings


def chunk_to_dict(doc) -> dict:
    """검색 청크를 contexts·출력용 dict로 바꿉니다."""
    meta = doc.metadata
    return {
        "rule_id": meta["rule_id"],
        "title": meta["title"],
        "source": meta["source"],
        "url": meta.get("url", ""),
        "text": doc.page_content,
    }


def build_tools(diff_files: list[dp.FileDiff], retriever, log: ToolLog) -> list:
    """리뷰 한 건의 diff에 묶인 도구 목록을 만듭니다.

    Args:
        diff_files: 입력 가드레일을 통과한(마스킹된) .py 파일 변경 목록
        retriever: `src.retriever.HybridRetriever`
            (테스트에서는 가짜 임베딩으로 만든 것)
        log: 도구 호출·출력을 기록할 `ToolLog`
    """
    by_path = {f.path: f for f in diff_files}

    @tool
    def ruff_check(file: str = "") -> str:
        """변경된 .py 파일에 ruff 린터를 돌려 **변경된 줄**의 규칙 위반을 돌려줍니다.

        스타일 문제(줄 길이 E501, import 순서 I001, 이름 규칙 N8xx, None 비교 E711 등)와
        사용하지 않는 import 같은 정적 분석 결과가 필요할 때 씁니다.
        `file`에 diff 안의 파일 경로를 주면 그 파일만,
        비우면 모든 .py 파일을 검사합니다.
        결과의 `rule_id`(예: "ruff:E501")는 근거 유형 tool로 인용하고,
        `pep8_rule_id`가 있으면 search_evidence로 그 PEP 8 섹션을 찾아
        doc 근거로 함께 인용하세요.
        """
        log.record("ruff_check", {"file": file})
        targets = [by_path[file]] if file in by_path else list(by_path.values())
        findings = [f for target in targets for f in run_ruff(target)]
        log.ruff_findings.extend(findings)
        return json.dumps(findings, ensure_ascii=False) if findings else "위반 없음"

    @tool
    def search_evidence(query: str) -> str:
        """PEP 8 규칙과 LangChain deprecated API 매핑표에서 근거 문서를 검색합니다.

        지적에 붙일 문서 근거가 필요할 때 씁니다. 예: 구버전 import 문
        ("from langchain.chains import LLMChain"), ruff 코드("E501 line too long"),
        규칙 이름("import 순서")으로 검색합니다. 결과마다 `rule_id`(예: "DEP-LLMChain",
        "PEP8-imports")가 있으며,
        지적의 근거(유형 doc)에는 이 결과에 나온 rule_id만 인용하세요.
        """
        log.record("search_evidence", {"query": query})
        chunks = [chunk_to_dict(d) for d in retriever.search(query)]
        for c in chunks:
            log.retrieved[c["rule_id"]] = c
        return json.dumps(
            [{k: c[k] for k in ("rule_id", "title", "text")} for c in chunks],
            ensure_ascii=False,
        )

    return [ruff_check, search_evidence]


def github_post_comment(repo: str, body: str, log: ToolLog) -> dict:
    """승인된 리뷰를 PR 코멘트로 게시합니다. **이번 범위는 dry-run**(내용만 돌려줌).

    LLM이 고르는 도구가 아니라 HITL 승인 뒤 게시 노드가 직접 부릅니다.
    허용 목록 확인은 호출하는 쪽(게시 노드)이 코드로 먼저 합니다.
    """
    log.record("github_post_comment", {"repo": repo, "dry_run": True})
    return {"repo": repo, "dry_run": True, "body": body}
