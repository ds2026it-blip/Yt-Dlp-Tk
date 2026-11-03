@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0stream_dl.py" %*
) else (
    python "%~dp0stream_dl.py" %*
)
