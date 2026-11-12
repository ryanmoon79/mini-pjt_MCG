"""diff 파싱 단일 모듈.

입력 가드레일(범위·300줄 판정), 렌더러(코드 발췌), 출력 검증(code 근거 실재 확인),
평가 스크립트(diff hunk 저장)가 모두 이 모듈만 써서 diff를 해석합니다.
LLM을 쓰지 않는 결정적 코드입니다 (CLAUDE.md 0번 "계산·규칙 판정은 파이썬 코드로").

다루는 형식: `git diff` 형식의 unified diff
(`diff --git a/x b/x`, `--- a/x`, `+++ b/x`, `@@ -a,b +c,d @@`).

용어:
- **변경 후 줄 번호**: 새 파일 기준 줄 번호. 지적과 평가가 모두 이 번호를 씁니다.
- **변경된 줄**: 추가된 줄의 변경 후 번호 + 삭제 지점 번호.
- **삭제 지점**: 연속 삭제 블록 바로 다음에 오는 변경 후 줄 번호.
  hunk가 삭제로 끝나고 뒤에 문맥 줄이 없으면(파일 끝 삭제)
  변경 후 파일의 마지막 줄 번호.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


_DEV_NULL = "/dev/null"


@dataclass
class DiffLine:
    """hunk 안의 한 줄. kind는 '+'(추가), '-'(삭제), ' '(문맥)."""

    kind: str
    text: str
    old_no: int | None  # 변경 전 줄 번호 (추가 줄은 None)
    new_no: int | None  # 변경 후 줄 번호 (삭제 줄은 None)


@dataclass
class Hunk:
    """`@@` 하나로 시작하는 변경 묶음."""

    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str  # `@@ ... @@` 줄 전체
    lines: list[DiffLine] = field(default_factory=list)

    def text(self) -> str:
        """hunk를 diff 원문 형태의 문자열로 돌려줍니다 (실행 기록·RAGAS 입력용)."""
        body = [f"{ln.kind}{ln.text}" for ln in self.lines]
        return "\n".join([self.header, *body])


@dataclass
class FileDiff:
    """파일 하나의 변경."""

    path: str  # 변경 후 경로 (삭제된 파일이면 변경 전 경로)
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def is_python(self) -> bool:
        """리뷰 대상(.py)인지 여부."""
        return self.path.endswith(".py")

    def text(self) -> str:
        """이 파일의 변경을 git diff 형식 문자열로 돌려줍니다 (마스킹 후 LLM 입력용)."""
        old = f"a/{self.old_path}" if self.old_path else "/dev/null"
        new = f"b/{self.new_path}" if self.new_path else "/dev/null"
        head = [f"diff --git a/{self.path} b/{self.path}", f"--- {old}", f"+++ {new}"]
        return "\n".join(head + [h.text() for h in self.hunks])

    @property
    def added_count(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "+")

    @property
    def deleted_count(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "-")


def _strip_prefix(raw: str) -> str | None:
    """`a/경로`·`b/경로`에서 접두어를 떼고, `/dev/null`이면 None을 돌려줍니다."""
    raw = raw.strip().split("\t")[0]
    if raw == _DEV_NULL:
        return None
    if raw.startswith(("a/", "b/")):
        return raw[2:]
    return raw


def parse_diff(text: str) -> list[FileDiff]:
    """unified diff 문자열을 파일별 변경 목록으로 나눕니다.

    `\\ No newline at end of file` 줄은 무시합니다. 헤더(`diff --git`, `index`,
    `---`, `+++`)는 줄 수에 포함하지 않습니다.
    """
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    old_left = new_left = 0  # 현재 hunk에서 아직 읽지 않은 변경 전·후 줄 수
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # hunk 안에서 읽을 줄이 남아 있으면 접두어와 관계없이 내용으로 읽음
        # ("--- "로 시작하는 삭제 줄을 파일 헤더로 오인하지 않기 위해)
        if hunk is not None and (old_left > 0 or new_left > 0):
            if line.startswith("\\"):  # "\ No newline at end of file"
                i += 1
                continue
            kind, body = (line[0], line[1:]) if line else (" ", "")
            if kind == "+":
                hunk.lines.append(DiffLine("+", body, None, new_no))
                new_no, new_left = new_no + 1, new_left - 1
            elif kind == "-":
                hunk.lines.append(DiffLine("-", body, old_no, None))
                old_no, old_left = old_no + 1, old_left - 1
            else:  # 문맥 줄 (빈 문맥 줄의 앞 공백이 지워진 경우 포함)
                hunk.lines.append(DiffLine(" ", body, old_no, new_no))
                old_no, new_no = old_no + 1, new_no + 1
                old_left, new_left = old_left - 1, new_left - 1
            i += 1
            continue

        if line.startswith("diff --git "):
            current, hunk = None, None
        elif (
            line.startswith("--- ")
            and i + 1 < len(lines)
            and lines[i + 1].startswith("+++ ")
        ):
            old_path = _strip_prefix(line[4:])
            new_path = _strip_prefix(lines[i + 1][4:])
            current = FileDiff(
                path=new_path or old_path or "",
                old_path=old_path,
                new_path=new_path,
            )
            files.append(current)
            hunk = None
            i += 1  # +++ 줄까지 소비
        elif (match := _HUNK_RE.match(line)) and current is not None:
            old_start, new_start = int(match.group(1)), int(match.group(3))
            old_len = int(match.group(2)) if match.group(2) is not None else 1
            new_len = int(match.group(4)) if match.group(4) is not None else 1
            hunk = Hunk(old_start, old_len, new_start, new_len, header=line)
            current.hunks.append(hunk)
            # 길이 0인 쪽은 시작 번호가 "그 앞 줄"을 가리키므로 다음 줄부터 셈
            old_no = old_start if old_len else old_start + 1
            new_no = new_start if new_len else new_start + 1
            old_left, new_left = old_len, new_len
        # 그 밖의 줄(`index ...`, `new file mode` 등)은 건너뜀
        i += 1
    return files


def split_python(files: list[FileDiff]) -> tuple[list[FileDiff], list[str]]:
    """리뷰 대상 `.py` 파일과 건너뛸 파일 경로 목록으로 나눕니다."""
    py = [f for f in files if f.is_python]
    skipped = [f.path for f in files if not f.is_python]
    return py, skipped


def py_changed_line_count(files: list[FileDiff]) -> int:
    """`.py` 파일의 추가·삭제 줄 합계 (300줄 판정용).

    diff 헤더·문맥 줄·`.py`가 아닌 파일의 줄은 세지 않습니다.
    """
    return sum(f.added_count + f.deleted_count for f in files if f.is_python)


def deletion_anchors(file: FileDiff) -> set[int]:
    """삭제 지점들의 변경 후 줄 번호 (모듈 설명의 "삭제 지점" 규칙)."""
    anchors: set[int] = set()
    for hunk in file.hunks:
        in_deletion = False
        for ln in hunk.lines:
            if ln.kind == "-":
                in_deletion = True
            elif in_deletion and ln.new_no is not None:
                anchors.add(ln.new_no)
                in_deletion = False
        if in_deletion:
            # hunk가 삭제로 끝남 = 파일 끝 삭제 → 변경 후 파일의 마지막 줄
            last = hunk.new_start + hunk.new_len - 1
            anchors.add(max(last, 1))
    return anchors


def changed_lines(file: FileDiff) -> set[int]:
    """변경된 줄(추가된 줄 + 삭제 지점)의 변경 후 줄 번호 집합."""
    added = {ln.new_no for h in file.hunks for ln in h.lines if ln.kind == "+"}
    return {n for n in added if n is not None} | deletion_anchors(file)


def new_side_lines(file: FileDiff) -> dict[int, str]:
    """diff 안에 보이는 변경 후 줄(추가·문맥)의 {줄 번호: 내용}."""
    return {
        ln.new_no: ln.text
        for h in file.hunks
        for ln in h.lines
        if ln.new_no is not None
    }


def excerpt(file: FileDiff, line: int, radius: int = 1) -> list[tuple[int, str]]:
    """변경 후 `line` 줄과 앞뒤 `radius`줄을 (줄 번호, 내용) 목록으로 돌려줍니다.

    diff에 보이지 않는 줄은 건너뜁니다. 렌더러의 "문제 코드" 표시에 씁니다.
    """
    visible = new_side_lines(file)
    window = range(line - radius, line + radius + 1)
    return [(n, visible[n]) for n in window if n in visible]


def hunk_for_line(file: FileDiff, line: int) -> Hunk | None:
    """변경 후 `line` 줄이 들어 있는 hunk를 돌려줍니다 (없으면 None)."""
    for hunk in file.hunks:
        end = hunk.new_start + max(hunk.new_len, 1) - 1
        if hunk.new_start <= line <= end:
            return hunk
    return None


def numbered_text(files: list[FileDiff]) -> str:
    """리뷰어 프롬프트용: 줄마다 변경 후 줄 번호를 붙인 diff.

    형식: `  12 + 추가된 줄`, `  13   문맥 줄`, `     - 삭제된 줄`.
    리뷰어가 지적의 `line`에 이 번호(변경 후 기준)를 그대로 쓰게 하려는 것입니다.
    """
    out: list[str] = []
    for f in files:
        out.append(f"### 파일: {f.path}")
        for h in f.hunks:
            for ln in h.lines:
                num = f"{ln.new_no:>4}" if ln.new_no is not None else "    "
                out.append(f"{num} {ln.kind} {ln.text}")
    return "\n".join(out)


def find_file(files: list[FileDiff], path: str) -> FileDiff | None:
    """경로로 파일 변경을 찾습니다."""
    return next((f for f in files if f.path == path), None)
