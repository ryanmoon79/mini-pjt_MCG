"""시연용 로컬 웹 GUI (TASKS G1, CLAUDE.md 6번 "GUI").

사용법:
    python -m src.gui_server            # http://127.0.0.1:8765 에서 열림
    python -m src.gui_server --port 9000 --no-browser

- 표준 라이브러리(`http.server`, `threading`, `json`, `subprocess`)만 씁니다.
- 에이전트 경로는 CLI와 같습니다: `run_query`로 리뷰하고, 승인 대기에서 멈춘 리뷰는
  `resume_review`로 재개합니다. 화면 결과는 `run_query`의 `answer`(렌더러 출력)를
  그대로 고정폭으로 보여 주고, 내부 로그는 `render.render_verbose`를 씁니다.
- 리뷰는 서버의 별도 스레드에서 돌고, 화면은 `/api/job`을 폴링해 진행 상황을 봅니다.
  `run_query`는 끝날 때 trace를 돌려주므로, 진행 중의 노드 진입 이벤트는
  이 프로세스 안에서만 `TraceRecorder.add`를 감싸 작업별로 복사해 둡니다
  (에이전트 코드는 고치지 않음, `contextvars`로 작업을 구분).
- 승인 대기 중인 `thread_id`는 서버 메모리에만 둡니다. 게시는 CLI와 같이 dry-run이며
  허용 목록(`CITEREVIEW_ALLOWED_POST_REPOS`) 밖 저장소는 그래프 코드가 막습니다.
- 서버는 127.0.0.1에만 바인딩합니다.
"""

from __future__ import annotations

import argparse
import contextvars
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src import config
from src.agent import resume_review, run_query
from src.observability import TraceRecorder
from src.render import render_verbose

HOST = "127.0.0.1"  # 로컬에서만 엶 (외부 주소로 바인딩하지 않음)
DEFAULT_PORT = 8765
MAX_PY_LINES = config.MAX_PY_CHANGED_LINES
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
WORK_DIR = Path(tempfile.mkdtemp(prefix="citereview_gui_"))  # 커밋에서 만든 diff

# 진행 표시에 쓰는 노드 순서와 화면 이름 (render 다음이 승인 대기)
STEPS = [
    ("input_guardrail", "입력 가드레일"),
    ("planner", "planner"),
    ("supervisor", "리뷰어"),  # review_team 노드의 trace 이름
    ("output_validate", "출력 검증"),
    ("synthesize", "종합"),
    ("render", "렌더링"),
    ("hitl", "승인 대기"),
]

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
_CURRENT_JOB: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "citereview_gui_job", default=None
)


# ── 진행 상황 수집 (에이전트 코드는 그대로, 이 프로세스에서만 감쌈) ──────────


def _install_progress_hook() -> None:
    """`TraceRecorder.add`를 감싸 노드 진입 이벤트를 현재 작업에도 남깁니다.

    LangGraph는 노드를 실행할 때 contextvars를 복사해 넘기므로, 리뷰 스레드에서
    정한 작업 ID가 리뷰어 스레드에서도 보입니다.
    """
    original = TraceRecorder.add
    if getattr(original, "_gui_hooked", False):
        return

    def add(self, type_, **fields):
        event = original(self, type_, **fields)
        job_id = _CURRENT_JOB.get()
        if job_id and type_ in {"node", "hitl"}:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is not None:
                    job["events"].append(
                        {"type": type_, "name": fields.get("name", type_)}
                    )
        return event

    add._gui_hooked = True  # type: ignore[attr-defined]
    TraceRecorder.add = add  # type: ignore[method-assign]


# ── git: 폴더 → 커밋 목록 → diff ─────────────────────────────────────


def _git(repo: str, *args: str) -> str:
    """저장소 폴더에서 git 명령을 실행하고 표준 출력을 돌려줍니다."""
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "git 명령이 실패했습니다.")
    return result.stdout


def list_commits(repo: str, limit: int = 30) -> list[dict]:
    """최근 커밋 목록과 커밋마다 `.py` 추가·삭제 줄 수(300줄 초과 표시용)."""
    out = _git(
        repo,
        "log",
        f"-n{limit}",
        "--numstat",
        "--format=@@%h%x09%ad%x09%s",
        "--date=short",
    )
    commits: list[dict] = []
    for line in out.splitlines():
        if line.startswith("@@"):
            sha, date, subject = (line[2:].split("\t", 2) + ["", ""])[:3]
            commits.append(
                {
                    "sha": sha,
                    "date": date,
                    "subject": subject,
                    "py_lines": 0,
                    "py_files": 0,
                }
            )
        elif line.strip() and commits:
            added, deleted, path = (line.split("\t", 2) + ["", ""])[:3]
            if path.endswith(".py") and added.isdigit() and deleted.isdigit():
                commits[-1]["py_lines"] += int(added) + int(deleted)
                commits[-1]["py_files"] += 1
    for c in commits:
        c["over_limit"] = c["py_lines"] > MAX_PY_LINES
    return commits


def diff_from_commit(repo: str, sha: str) -> Path:
    """커밋 하나의 변경을 전체 문맥(-U9999) diff 파일로 저장합니다 (임시 폴더)."""
    if not COMMIT_RE.match(sha):
        raise ValueError("커밋 해시 형식이 아닙니다.")
    name = re.sub(r"[^A-Za-z0-9_-]", "_", Path(repo).name) or "repo"
    path = WORK_DIR / f"{name}_{sha}.diff"
    _git(repo, "show", "-U9999", "--format=", f"--output={path}", sha)
    return path


def stage_diff_file(path: str) -> Path:
    """선택한 diff 파일을 임시 폴더로 복사합니다 (공백 없는 경로로 넘기기 위해)."""
    src = Path(path)
    if not src.is_absolute():
        src = config.ROOT_DIR / src
    if src.suffix != ".diff" or not src.is_file():
        raise ValueError(f"diff 파일을 찾지 못했습니다: {path}")
    dest = WORK_DIR / uuid.uuid4().hex[:8] / re.sub(r"\s+", "_", src.name)
    dest.parent.mkdir(parents=True)
    shutil.copyfile(src, dest)
    return dest


# ── 리뷰 작업 ────────────────────────────────────────────────────────


def start_job(diff_path: Path) -> str:
    """리뷰를 별도 스레드에서 시작하고 작업 ID를 돌려줍니다."""
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "events": [],
            "answer": "",
            "verbose": "",
            "thread_id": None,
            "post": None,
            "error": None,
            "title": diff_path.name,
        }

    def work() -> None:
        _CURRENT_JOB.set(job_id)
        try:
            result = run_query(f"{diff_path} 를 리뷰해줘")
            waiting = next(
                (
                    e
                    for e in result["trace"]
                    if e["type"] == "hitl" and e.get("status") == "waiting"
                ),
                None,
            )
            update = {
                "answer": result["answer"],
                "verbose": render_verbose(result["trace"]),
                "status": "waiting" if waiting else "done",
                "thread_id": waiting["thread_id"] if waiting else None,
            }
        except Exception as exc:  # 화면에 오류를 보여 주고 서버는 계속 동작
            update = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        with JOBS_LOCK:
            JOBS[job_id].update(update)

    threading.Thread(target=work, daemon=True).start()
    return job_id


def decide(job_id: str, action: str, repo: str, ignore: list[int]) -> dict:
    """승인/거절 결정을 `resume_review`로 넘깁니다 (게시는 dry-run + 허용 목록)."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None or job["status"] != "waiting":
            raise ValueError("승인 대기 중인 리뷰가 아닙니다.")
        job["status"] = "deciding"
        thread_id = job["thread_id"]
    decision = {
        "action": "approve" if action == "approve" else "reject",
        "ignore": ignore,
        "repo": repo or None,
    }
    try:
        outcome = resume_review(thread_id, decision)["post"]
    except Exception as exc:
        with JOBS_LOCK:
            job["status"] = "waiting"
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc
    with JOBS_LOCK:
        job["post"] = outcome
        job["status"] = "done"
    return outcome


def job_view(job_id: str) -> dict:
    """화면이 폴링하는 작업 상태 (진행 단계 포함)."""
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    if not job:
        raise ValueError("작업을 찾지 못했습니다.")
    seen = {e["name"] for e in job["events"]} | {
        "hitl" for e in job["events"] if e["type"] == "hitl"
    }
    job["steps"] = [{"key": k, "label": label, "done": k in seen} for k, label in STEPS]
    job.pop("events")
    return job


def eval_diffs() -> list[str]:
    """평가용 diff 목록 (선택 상자용, 프로젝트 기준 상대 경로)."""
    return sorted(
        str(p.relative_to(config.ROOT_DIR)).replace("\\", "/")
        for p in config.EVAL_DIFFS_DIR.glob("*.diff")
    )


# ── HTTP ─────────────────────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    """화면(HTML)과 JSON API를 제공합니다."""

    def log_message(self, fmt, *args):  # 요청 로그는 조용히
        return

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):  # noqa: N802 (http.server 규약)
        """화면, diff 목록, 작업 상태."""
        url = urlparse(self.path)
        try:
            if url.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif url.path == "/api/diffs":
                self._json({"diffs": eval_diffs()})
            elif url.path == "/api/job":
                job_id = parse_qs(url.query).get("id", [""])[0]
                self._json(job_view(job_id))
            else:
                self._json({"error": "없는 경로"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self):  # noqa: N802 (http.server 규약)
        """커밋 목록, 리뷰 시작, 승인/거절."""
        try:
            data = self._body()
            if self.path == "/api/commits":
                self._json({"commits": list_commits(data["repo"].strip())})
            elif self.path == "/api/review":
                if data.get("mode") == "commit":
                    path = diff_from_commit(data["repo"].strip(), data["sha"].strip())
                else:
                    path = stage_diff_file(data["path"].strip())
                self._json({"job": start_job(path)})
            elif self.path == "/api/decision":
                ignore = [int(n) for n in re.findall(r"\d+", data.get("ignore", ""))]
                post = decide(data["job"], data["action"], data.get("repo", ""), ignore)
                self._json({"post": post})
            else:
                self._json({"error": "없는 경로"}, 404)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)


PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CiteReview</title>
<meta name="description" content="근거를 다는 코드 리뷰 에이전트 CiteReview의 로컬 시연 화면: 커밋이나 diff를 골라 리뷰하고 게시를 승인합니다.">
<style>
/* Hallmark · shape: app screen · genre: modern-minimal · theme: custom · redesign: full
 * paper: oklch(97.5% 0.006 85) · accent: oklch(52% 0.07 190) · display: system sans · body: system sans
 * nav: top bar, wordmark and run status · signature: the review ledger, CLI output in a numbered monospace sheet · states: empty, loading, error, waiting, done
 * axes: warm paper/system sans/muted teal · posture: workbench, sources left, ledger right · contrast: pass · v2.0.0 */
/* Hallmark · pre-emit critique: P4 H5 E4 S4 R5 V3 · weakest: V, a two-pane tool is the expected shape; kept it because the ledger must stay wide, and spent the variety on the step track drawn as a rule with notches instead of pills */
:root {
  --space-2xs: 4px; --space-xs: 8px; --space-sm: 12px; --space-md: 16px; --space-lg: 24px; --space-xl: 40px;
  --text-xs: 0.8125rem; --text-sm: 0.875rem; --text-md: 1rem; --text-lg: 1.125rem; --text-xl: 1.35rem;
  --dur-micro: 120ms; --dur-short: 220ms; --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --container: 1440px; --gutter: 32px; --control-h: 36px; --radius: 6px; --rule-hair: 1px;
  --color-paper: oklch(97.5% 0.006 85);
  --color-paper-2: oklch(94.5% 0.008 85);
  --color-paper-3: oklch(99% 0.003 85);
  --color-ink: oklch(24% 0.012 85);
  --color-ink-2: oklch(38% 0.012 85);
  --color-muted: oklch(52% 0.012 85);
  --color-rule: oklch(87% 0.01 85);
  --color-accent: oklch(52% 0.07 190);
  --color-accent-hover: oklch(46% 0.07 190);
  --color-accent-ink: oklch(99% 0.005 190);
  --color-accent-soft: oklch(93% 0.03 190);
  --color-focus: oklch(55% 0.12 240);
  --color-error: oklch(50% 0.17 27);
  --color-error-soft: oklch(95% 0.03 27);
  --color-ledger: oklch(99% 0.003 85);
  --font-body: system-ui, "Malgun Gothic", "Apple SD Gothic Neo", sans-serif;
  --font-mono: "D2Coding", Consolas, "Cascadia Mono", ui-monospace, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --color-paper: oklch(20% 0.01 85);
    --color-paper-2: oklch(25% 0.01 85);
    --color-paper-3: oklch(23% 0.01 85);
    --color-ink: oklch(92% 0.008 85);
    --color-ink-2: oklch(80% 0.008 85);
    --color-muted: oklch(66% 0.01 85);
    --color-rule: oklch(34% 0.01 85);
    --color-accent: oklch(70% 0.08 190);
    --color-accent-hover: oklch(76% 0.08 190);
    --color-accent-ink: oklch(18% 0.02 190);
    --color-accent-soft: oklch(30% 0.04 190);
    --color-focus: oklch(72% 0.12 240);
    --color-error: oklch(72% 0.14 27);
    --color-error-soft: oklch(28% 0.05 27);
    --color-ledger: oklch(17% 0.01 85);
  }
}
*, *::before, *::after { box-sizing: border-box; }
html, body { overflow-x: clip; }
body {
  margin: 0; background: var(--color-paper); color: var(--color-ink);
  font-family: var(--font-body); font-size: var(--text-md); line-height: 1.5;
  caret-color: var(--color-accent);
}
:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
[hidden] { display: none !important; }
button, input, select { font: inherit; }
h1, h2 { margin: 0; overflow-wrap: break-word; min-width: 0; text-wrap: balance; }
html { scrollbar-color: var(--color-muted) var(--color-paper); }
::selection { background: var(--color-accent-soft); color: var(--color-ink); }

.bar {
  border-bottom: var(--rule-hair) solid var(--color-rule); background: var(--color-paper-3);
}
.bar-inner, .work {
  width: 100%; max-width: var(--container); margin-inline: auto; padding-inline: var(--gutter);
}
.bar-inner { display: flex; align-items: baseline; gap: var(--space-md); padding-block: var(--space-sm); }
.bar h1 { font-size: var(--text-lg); font-weight: 650; }
.bar p { margin: 0; color: var(--color-muted); font-size: var(--text-sm); }

.work {
  display: grid; grid-template-columns: minmax(0, 22rem) minmax(0, 1fr);
  gap: var(--space-lg); padding-block: var(--space-lg);
}
.panel {
  background: var(--color-paper-3); border: var(--rule-hair) solid var(--color-rule);
  border-radius: var(--radius); padding: var(--space-md); color: var(--color-ink);
  display: grid; gap: var(--space-sm); align-content: start; min-width: 0;
}
.side { display: grid; gap: var(--space-lg); align-content: start; min-width: 0; }
.main { display: grid; gap: var(--space-lg); align-content: start; min-width: 0; }
.panel h2 { font-size: var(--text-md); font-weight: 650; }
.hint { margin: 0; color: var(--color-muted); font-size: var(--text-sm); }
label.field { display: grid; gap: var(--space-2xs); font-size: var(--text-sm); color: var(--color-ink-2); }
.row { display: flex; gap: var(--space-xs); flex-wrap: wrap; align-items: center; }

input[type=text], select {
  width: 100%; min-height: var(--control-h); padding: 0 var(--space-sm);
  border: var(--rule-hair) solid var(--color-rule); border-radius: var(--radius);
  background: var(--color-paper); color: var(--color-ink);
  transition: border-color var(--dur-micro) var(--ease-out);
}
input[type=text]::placeholder { color: var(--color-muted); }
input[type=text]:hover, select:hover { border-color: var(--color-muted); }
input[type=text]:disabled, select:disabled { opacity: 0.55; cursor: not-allowed; }

button {
  min-height: var(--control-h); padding: 0 var(--space-md); border-radius: var(--radius);
  border: var(--rule-hair) solid var(--color-rule); background: var(--color-paper);
  color: var(--color-ink); cursor: pointer;
  transition: background-color var(--dur-micro) var(--ease-out),
    border-color var(--dur-micro) var(--ease-out), transform var(--dur-micro) var(--ease-out);
}
button:hover { background: var(--color-paper-2); border-color: var(--color-muted); }
button:active { transform: translateY(1px); }
button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
button.primary {
  background: var(--color-accent); border-color: var(--color-accent); color: var(--color-accent-ink);
}
button.primary:hover { background: var(--color-accent-hover); border-color: var(--color-accent-hover); }
.side button.primary { justify-self: start; }

#commits:empty::before { content: "폴더를 넣고 커밋을 불러오면 최근 30개가 여기에 나옵니다."; color: var(--color-muted); font-size: var(--text-sm); }
#commits { max-height: 26rem; overflow: auto; font-size: var(--text-sm); }
#commits table { width: 100%; border-collapse: collapse; }
#commits th, #commits td {
  text-align: left; padding: var(--space-2xs) var(--space-xs);
  border-bottom: var(--rule-hair) solid var(--color-rule); vertical-align: top;
}
#commits th { font-weight: 600; color: var(--color-ink-2); background: var(--color-paper-2); position: sticky; top: 0; }
#commits td:first-child { font-family: var(--font-mono); }
#commits td:nth-child(4) { font-variant-numeric: tabular-nums; white-space: nowrap; }
#commits tr.over td { color: var(--color-error); }
#commits .err { color: var(--color-error); }
#commits td button { min-height: 28px; padding: 0 var(--space-sm); }

.run-head { display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-md); flex-wrap: wrap; }
#title { font-family: var(--font-mono); font-size: var(--text-sm); color: var(--color-ink-2); overflow-wrap: anywhere; }
#title:empty::before { content: "아직 실행한 리뷰가 없습니다."; font-family: var(--font-body); color: var(--color-muted); }
#status { font-size: var(--text-sm); color: var(--color-ink-2); }
#status.err { color: var(--color-error); }

#steps { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); }
#steps li {
  position: relative; padding-top: var(--space-md); font-size: var(--text-xs);
  color: var(--color-muted); border-top: 2px solid var(--color-rule); min-width: 0;
}
#steps li::before {
  content: ""; position: absolute; top: -6px; left: 0; width: 10px; height: 10px;
  border-radius: 50%; background: var(--color-paper-3); border: 2px solid var(--color-rule);
}
#steps li.done { color: var(--color-ink); border-top-color: var(--color-accent); }
#steps li.done::before { background: var(--color-accent); border-color: var(--color-accent); }

.ledger-head { display: flex; justify-content: space-between; align-items: center; gap: var(--space-md); }
.check { display: inline-flex; gap: var(--space-xs); align-items: center; font-size: var(--text-sm); color: var(--color-ink-2); cursor: pointer; }
.check input { width: 16px; height: 16px; accent-color: var(--color-accent); }
pre {
  margin: 0; font-family: var(--font-mono); font-size: var(--text-sm); line-height: 1.55;
  white-space: pre; overflow-x: auto; tab-size: 4;
  background: var(--color-ledger); color: var(--color-ink);
  border: var(--rule-hair) solid var(--color-rule); border-radius: var(--radius);
  padding: var(--space-md);
}
#answer { min-height: 18rem; max-height: 70vh; overflow-y: auto; }
#answer:empty::before { content: "리뷰를 시작하면 CLI와 같은 결과가 여기에 나옵니다."; font-family: var(--font-body); color: var(--color-muted); }
#verbose { max-height: 40vh; overflow-y: auto; color: var(--color-ink-2); }

#decision { border-color: var(--color-accent); background: var(--color-accent-soft); }
#decision .fields { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: var(--space-sm); }
#post:empty { display: none; }

@media (max-width: 60rem) {
  :root { --gutter: 16px; }
  .work { grid-template-columns: minmax(0, 1fr); }
  #steps { grid-template-columns: repeat(4, minmax(0, 1fr)); row-gap: var(--space-md); }
  #decision .fields { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: 0.01ms !important; }
}
</style>
</head>
<body>
<header class="bar">
  <div class="bar-inner">
    <h1>CiteReview</h1>
    <p>근거를 다는 코드 리뷰, 로컬 실행 (127.0.0.1)</p>
  </div>
</header>

<main class="work">
  <div class="side">
    <section class="panel" aria-labelledby="h-commit">
      <h2 id="h-commit">저장소 커밋</h2>
      <label class="field">저장소 폴더
        <input type="text" id="repo" placeholder="C:\path\to\repo">
      </label>
      <button id="load-commits">커밋 불러오기</button>
      <div id="commits"></div>
    </section>

    <section class="panel" aria-labelledby="h-file">
      <h2 id="h-file">diff 파일</h2>
      <label class="field">평가용 diff
        <select id="diff-list"><option value="">선택 안 함</option></select>
      </label>
      <label class="field">파일 경로
        <input type="text" id="diff-path" placeholder="data/eval_diffs/03_llmchain.diff">
      </label>
      <button id="review-file" class="primary">리뷰</button>
    </section>
  </div>

  <div class="main">
    <section class="panel" aria-labelledby="h-run">
      <div class="run-head">
        <h2 id="h-run">진행</h2>
        <div id="status" role="status" aria-live="polite"></div>
      </div>
      <div id="title"></div>
      <ol id="steps"></ol>
    </section>

    <section class="panel" aria-labelledby="h-result">
      <div class="ledger-head">
        <h2 id="h-result">결과</h2>
        <label class="check"><input type="checkbox" id="show-verbose"> 내부 로그</label>
      </div>
      <pre id="answer"></pre>
      <pre id="verbose" hidden></pre>
    </section>

    <section class="panel" id="decision" aria-labelledby="h-decision" hidden>
      <h2 id="h-decision">게시 승인 (dry-run)</h2>
      <p class="hint">승인하기 전에는 게시하지 않습니다. 허용 목록에 없는 저장소는 막힙니다.</p>
      <div class="fields">
        <label class="field">게시 저장소
          <input type="text" id="post-repo" placeholder="owner/repo">
        </label>
        <label class="field">뺄 항목 번호
          <input type="text" id="ignore" placeholder="예: 2 3">
        </label>
      </div>
      <div class="row">
        <button id="approve" class="primary">승인</button>
        <button id="reject">거절</button>
      </div>
      <pre id="post"></pre>
    </section>
  </div>
</main>

<script>
const $ = (id) => document.getElementById(id);
const STEP_LABELS = ["입력 가드레일", "planner", "리뷰어", "출력 검증", "종합", "렌더링",
                     "승인 대기"];
let job = null, timer = null;

async function api(path, body) {
  const opt = body ? {method: "POST", body: JSON.stringify(body)} : {};
  const res = await fetch(path, opt);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function setStatus(text, isErr) {
  $("status").textContent = text; $("status").className = isErr ? "err" : "";
}
function drawSteps(steps) {
  $("steps").innerHTML = steps.map((s) =>
    `<li class="${s.done ? "done" : ""}">${s.label}</li>`).join("");
}

async function loadDiffs() {
  const data = await api("/api/diffs");
  for (const d of data.diffs) {
    const o = document.createElement("option"); o.value = d; o.textContent = d;
    $("diff-list").appendChild(o);
  }
}
$("diff-list").onchange = () => { $("diff-path").value = $("diff-list").value; };

$("load-commits").onclick = async () => {
  $("commits").textContent = "불러오는 중…";
  $("load-commits").disabled = true;
  try {
    const data = await api("/api/commits", {repo: $("repo").value});
    if (!data.commits.length) {
      $("commits").textContent = "이 폴더에 커밋이 없습니다.";
      return;
    }
    const rows = data.commits.map((c) =>
      `<tr class="${c.over_limit ? "over" : ""}"><td>${c.sha}</td><td>${c.date}</td>` +
      `<td>${escapeHtml(c.subject)}</td><td>.py ${c.py_files}개, ${c.py_lines}줄` +
      `${c.over_limit ? " (300줄 초과)" : ""}</td>` +
      `<td><button data-sha="${c.sha}">리뷰</button></td></tr>`).join("");
    $("commits").innerHTML = `<table><tr><th>커밋</th><th>날짜</th><th>메시지</th>` +
      `<th>변경</th><th></th></tr>${rows}</table>`;
    for (const b of $("commits").querySelectorAll("button")) {
      b.onclick = () => startReview({mode: "commit", repo: $("repo").value,
                                     sha: b.dataset.sha});
    }
  } catch (e) {
    $("commits").innerHTML = `<span class="err">${escapeHtml(e.message)}</span>`;
  } finally {
    $("load-commits").disabled = false;
  }
};
$("review-file").onclick = () =>
  startReview({mode: "file", path: $("diff-path").value});

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}

async function startReview(body) {
  clearInterval(timer);
  $("answer").textContent = ""; $("verbose").textContent = "";
  $("post").textContent = ""; $("decision").hidden = true;
  $("approve").disabled = false; $("reject").disabled = false;
  drawSteps(STEP_LABELS.map((label) => ({label, done: false})));
  setStatus("리뷰 시작…");
  try {
    job = (await api("/api/review", body)).job;
    timer = setInterval(poll, 700); poll();
  } catch (e) { setStatus(e.message, true); }
}

async function poll() {
  let j;
  try {
    j = await api("/api/job?id=" + job);
  } catch (e) { return setStatus(e.message, true); }
  $("title").textContent = j.title;
  drawSteps(j.steps);
  if (j.status === "running") return setStatus("실행 중…");
  clearInterval(timer);
  if (j.status === "error") return setStatus(j.error, true);
  $("answer").textContent = j.answer;
  $("verbose").textContent = j.verbose;
  if (j.status === "waiting") {
    setStatus("승인 대기 중");
    $("decision").hidden = false;
  } else { setStatus("완료"); }
}

$("show-verbose").onchange = () => {
  $("verbose").hidden = !$("show-verbose").checked;
};

async function sendDecision(action) {
  $("approve").disabled = true; $("reject").disabled = true;
  try {
    const data = await api("/api/decision", {job, action, repo: $("post-repo").value,
                                             ignore: $("ignore").value});
    const p = data.post;
    $("post").textContent = p.status === "posted"
      ? `[dry-run] ${p.repo}에 게시할 코멘트:\n\n${p.body}`
      : `게시하지 않았습니다: ${p.reason}`;
    setStatus("완료");
  } catch (e) {
    setStatus(e.message, true);
    $("approve").disabled = false; $("reject").disabled = false;
  }
}
$("approve").onclick = () => sendDecision("approve");
$("reject").onclick = () => sendDecision("reject");

drawSteps(STEP_LABELS.map((label) => ({label, done: false})));
loadDiffs();
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    """GUI 서버를 127.0.0.1에서 열고 (기본) 브라우저를 띄웁니다."""
    parser = argparse.ArgumentParser(description="CiteReview 시연용 로컬 GUI")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-browser", action="store_true", help="브라우저를 열지 않음"
    )
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    config.load_env()
    _install_progress_hook()
    server = ThreadingHTTPServer((HOST, args.port), Handler)
    url = f"http://{HOST}:{args.port}"
    print(f"CiteReview GUI: {url}  (Ctrl+C로 종료)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
