@echo off
rem Windows launcher for running from the source files (the counterpart of run.sh).
rem Installs Python if needed, sets up a local .venv, installs requirements, starts the app.
setlocal
cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"

rem 1. Find Python 3 (the py launcher first, then python on PATH)
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo Python 3 was not found. Installing it with winget (per-user, no admin needed^)...
    where winget >nul 2>nul
    if errorlevel 1 (
        echo winget is not available. Please install Python 3 from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH", then run this file again.
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo Python installation failed. Please install it from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    rem winget doesn't refresh this window's PATH; use the default per-user install location
    set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)

rem 2. Virtual environment
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating local virtual environment (.venv^)...
    %PY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Could not create the virtual environment.
        pause
        exit /b 1
    )
)

rem 3. Python packages
echo Checking required Python libraries...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip --quiet
"%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo Installing Python libraries failed. Check your internet connection and try again.
    pause
    exit /b 1
)

rem 4. Run without a console window
echo Launching JoshW-Browser
start "" "%VENV_DIR%\Scripts\pythonw.exe" main.py
endlocal
