@echo off
rem CiteReview 시연용 GUI 실행 (더블클릭). 127.0.0.1:8765에서 열고 브라우저를 띄웁니다.
rem 활성화된 가상환경(.venv-eval 등)과 관계없이 리뷰 본체 환경의 Python으로 실행합니다.
rem 찾는 순서: 프로젝트 venv(.venv) -> 과정 venv(..\..\.venv) -> PATH의 python (.venv-eval이면 건너뜀)
chcp 65001 > nul
setlocal
cd /d "%~dp0"
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0..\..\.venv\Scripts\python.exe" set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PY (
      echo %%P | findstr /i /c:".venv-eval" > nul || set "PY=%%P"
    )
  )
)
if not defined PY (
  echo 리뷰 본체 환경의 Python을 찾지 못했습니다. README 실행 방법 1번으로 환경을 만들어 주세요.
  pause
  exit /b 1
)
rem 본체 환경인지 확인: 에이전트 의존성(langgraph)이 import되어야 함
"%PY%" -c "import langgraph, langchain" > nul 2>&1
if errorlevel 1 (
  echo %PY% 에 리뷰 의존성이 없습니다. "%PY% -m pip install -r requirements.txt" 후 다시 실행하세요.
  pause
  exit /b 1
)
echo Python: %PY%
"%PY%" -m src.interfaces.gui_server %*
pause
