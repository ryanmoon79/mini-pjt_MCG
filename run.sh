#!/usr/bin/env bash
# CiteReview 시연용 GUI 실행 (맥·리눅스, Windows Git Bash). 127.0.0.1:8765에서 열고 브라우저를 띄웁니다.
# run.bat과 같은 동작: 활성화된 가상환경과 관계없이 프로젝트 가상환경(.venv)의 Python으로 실행합니다.
# 추가 인자는 GUI 서버에 그대로 넘깁니다 (예: bash run.sh --no-browser --port 8799).
set -euo pipefail
cd "$(dirname "$0")"

PY=""
for candidate in ".venv/bin/python" ".venv/Scripts/python.exe"; do
  if [ -x "$candidate" ]; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo "프로젝트 가상환경(.venv)을 찾지 못했습니다. README 실행 방법 1번으로 만들어 주세요:"
  echo "  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

# 본체 환경인지 확인: 에이전트 의존성(langgraph)이 import되어야 함
if ! "$PY" -c "import langgraph, langchain" > /dev/null 2>&1; then
  echo "$PY 에 리뷰 의존성이 없습니다. \"$PY -m pip install -r requirements.txt\" 후 다시 실행하세요."
  exit 1
fi

echo "Python: $PY"
exec "$PY" -m src.interfaces.gui_server "$@"
