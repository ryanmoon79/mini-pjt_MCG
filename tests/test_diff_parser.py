"""src/diff_parser.py 단위 테스트 (TASKS A2).

테스트용 diff는 이 파일 안에서 만듭니다 (교육용 더미 데이터입니다).
"""

from src import diff_parser as dp
from src.config import MAX_PY_CHANGED_LINES


def _added_file(path: str, n: int) -> str:
    """새 파일 `path`에 n줄을 추가하는 diff."""
    body = "\n".join(f"+value_{i} = {i}" for i in range(n))
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{n} @@\n"
        f"{body}\n"
    )


def test_300_lines_is_within_limit_and_301_exceeds():
    """.py 추가·삭제 합계 300줄은 통과, 301줄은 초과 (diff 헤더는 세지 않음)."""
    count_300 = dp.py_changed_line_count(dp.parse_diff(_added_file("app/a.py", 300)))
    count_301 = dp.py_changed_line_count(dp.parse_diff(_added_file("app/a.py", 301)))
    assert count_300 == 300 and count_300 <= MAX_PY_CHANGED_LINES
    assert count_301 == 301 and count_301 > MAX_PY_CHANGED_LINES


def test_mixed_py_and_md_counts_only_py():
    """.py와 .md가 섞이면 .md는 건너뛴 목록에 들어가고 줄 수에서 빠짐."""
    files = dp.parse_diff(_added_file("app/a.py", 2) + _added_file("README.md", 5))
    py, skipped = dp.split_python(files)
    assert [f.path for f in py] == ["app/a.py"]
    assert skipped == ["README.md"]
    assert dp.py_changed_line_count(files) == 2


DELETE_MIDDLE = """\
diff --git a/app/s.py b/app/s.py
--- a/app/s.py
+++ b/app/s.py
@@ -1,6 +1,4 @@
 a = 1
 b = 2
-if user is None:
-    return None
 e = 5
 f = 6
"""

DELETE_AT_EOF = """\
diff --git a/app/t.py b/app/t.py
--- a/app/t.py
+++ b/app/t.py
@@ -1,4 +1,2 @@
 a = 1
 b = 2
-c = 3
-d = 4
"""


def test_deletion_anchor_is_next_line_after_deletion():
    """삭제 지점은 삭제 블록 바로 다음 줄의 변경 후 번호 (e = 5 → 3행)."""
    (f,) = dp.parse_diff(DELETE_MIDDLE)
    assert dp.deletion_anchors(f) == {3}
    assert dp.changed_lines(f) == {3}
    assert dp.py_changed_line_count([f]) == 2  # 문맥 줄은 세지 않음


def test_deletion_at_end_of_file_anchors_last_line():
    """파일 끝 삭제는 변경 후 파일의 마지막 줄 (2행)."""
    (f,) = dp.parse_diff(DELETE_AT_EOF)
    assert dp.deletion_anchors(f) == {2}


MODIFY = """\
diff --git a/app/chain.py b/app/chain.py
index 111..222 100644
--- a/app/chain.py
+++ b/app/chain.py
@@ -10,4 +10,4 @@ def build():
 from langchain_core.prompts import ChatPromptTemplate
-from langchain_core.runnables import RunnableLambda
+from langchain.chains import LLMChain

 chain = None
\\ No newline at end of file
"""


def test_line_numbers_and_excerpt():
    """변경 후 줄 번호 매핑과 ±1줄 발췌 (빈 문맥 줄·No newline 표시 처리 포함)."""
    (f,) = dp.parse_diff(MODIFY)
    assert f.path == "app/chain.py"
    assert dp.changed_lines(f) == {11}
    assert dp.excerpt(f, 11) == [
        (10, "from langchain_core.prompts import ChatPromptTemplate"),
        (11, "from langchain.chains import LLMChain"),
        (12, ""),
    ]
    hunk = dp.hunk_for_line(f, 11)
    assert hunk is not None and hunk.text().startswith("@@ -10,4 +10,4 @@")


def test_header_like_lines_inside_hunk_are_content():
    """hunk 안의 '--- '·'+++ '로 시작하는 줄은 파일 헤더가 아니라 삭제·추가 내용."""
    text = (
        "diff --git a/app/q.py b/app/q.py\n"
        "--- a/app/q.py\n"
        "+++ b/app/q.py\n"
        "@@ -1,1 +1,1 @@\n"
        "--- old marker\n"
        "+++ new marker\n"
    )
    files = dp.parse_diff(text)
    assert len(files) == 1
    assert [(ln.kind, ln.text) for ln in files[0].hunks[0].lines] == [
        ("-", "-- old marker"),
        ("+", "++ new marker"),
    ]
