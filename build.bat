@echo off
rem Builds dist\Transcriber-Setup-<version>.exe and a portable zip. See tools\build.py.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install -q -r requirements-dev.txt || exit /b 1
".venv\Scripts\python.exe" tools\build.py %*
