@echo off
rem One-time setup on any PC (this one, the laptop, or after moving the folder):
rem   1. Python environment (.venv) and packages, if missing
rem   2. "Valence EMC Scanner" shortcuts in the Start Menu and on the Desktop
rem Then pin it: Start Menu > right-click "Valence EMC Scanner" > Pin to taskbar.
rem Needs Python 3.11+ (python.org) on the PC. Safe to run again.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Creating the Python environment...
    py -3.12 -m venv .venv 2>nul
    if not exist ".venv\Scripts\pythonw.exe" py -3 -m venv .venv 2>nul
    if not exist ".venv\Scripts\pythonw.exe" python -m venv .venv
)
if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo Python not found. Install Python 3.12 from python.org, then run this again.
    pause
    exit /b 1
)
echo Installing packages (the first time takes a few minutes)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -e .
if errorlevel 1 (
    rem Offline PCs (the scan PC behind its firewall) cannot fetch build tools.
    rem If the app is already installed, keep it and carry on.
    ".venv\Scripts\python.exe" -c "import emcscan" 2>nul
    if errorlevel 1 (
        echo.
        echo Package install failed, see above.
        pause
        exit /b 1
    )
    echo.
    echo No internet for the package update; the installed packages are kept.
)
echo Making shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_shortcut.ps1" -Repo "%~dp0."
if errorlevel 1 (
    pause
    exit /b 1
)
echo.
echo Done. Start Menu: right-click "Valence EMC Scanner" and choose Pin to taskbar.
pause
