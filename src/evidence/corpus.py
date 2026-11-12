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
from pathlib import Path

from langchain_core.documents import Document

from src.shared import config

PEP8_PATH = config.CORPUS_DIR / "pep8.md"


DEPRECATED_PATH = config.CORPUS_DIR / "deprecated_map.md"


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
