@echo off
setlocal
cd /d "%~dp0"
echo.
echo  ==== Transcriber setup ====
echo.

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
%PY% --version >nul 2>nul || (
    echo Python 3.10+ is required. Install it from https://www.python.org/downloads/ and run this again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv || (pause & exit /b 1)
)

echo Installing dependencies (this can take a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q || (pause & exit /b 1)
".venv\Scripts\python.exe" tools\make_icon.py

echo Creating desktop shortcut...
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Transcriber.lnk');" ^
  "$s.TargetPath='%~dp0.venv\Scripts\pythonw.exe';" ^
  "$s.Arguments='\"%~dp0main.py\"';" ^
  "$s.WorkingDirectory='%~dp0';" ^
  "$s.IconLocation='%~dp0assets\icon.ico';" ^
  "$s.Save()"

echo.
echo  Done! Open Transcriber from the desktop shortcut or Transcriber.bat
echo.
endlocal
