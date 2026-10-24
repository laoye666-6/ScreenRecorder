@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo [!] Venv not found. Creating...
    python -m venv .venv
)
echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo Done.
pause
