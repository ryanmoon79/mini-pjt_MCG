@echo off
rem CiteReview 시연용 GUI 실행 (더블클릭). 127.0.0.1:8765에서 열고 브라우저를 띄웁니다.
rem 과정 venv(..\..\.venv)가 있으면 그 Python을, 없으면 PATH의 python을 씁니다.
chcp 65001 > nul
cd /d "%~dp0"
set "PY=python"
if exist "%~dp0..\..\.venv\Scripts\python.exe" set "PY=%~dp0..\..\.venv\Scripts\python.exe"
"%PY%" -m src.gui_server %*
pause
