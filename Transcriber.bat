@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Transcriber isn't installed yet. Running setup first...
    call "%~dp0setup.bat"
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
