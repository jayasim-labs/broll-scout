@echo off
title B-Roll Scout Companion
color 0A

echo.
echo  B-Roll Scout Companion
echo  ──────────────────────
echo  Keep this window open while using broll.jayasim.com
echo.

set COMPANION_DIR=%~dp0
set VENV_DIR=%COMPANION_DIR%.venv

:: Auto-run setup if not yet installed
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  First launch detected — running setup...
    echo.
    call "%COMPANION_DIR%setup.bat"
    if %ERRORLEVEL% neq 0 (
        echo  Setup failed. Please try running setup.bat manually.
        pause
        exit /b 1
    )
    echo.
    echo  Setup complete! Starting companion now...
    echo.
)

:: Activate venv
call "%VENV_DIR%\Scripts\activate.bat"

:: Quick health check
python -c "import flask" 2>nul
if %ERRORLEVEL% neq 0 (
    echo  Dependencies missing. Re-running setup...
    call "%COMPANION_DIR%setup.bat"
    call "%VENV_DIR%\Scripts\activate.bat"
)

:: Auto-update yt-dlp silently (YouTube changes frequently)
echo  Checking for yt-dlp updates...
pip install --upgrade yt-dlp --quiet 2>nul
echo  OK
echo.

echo  Starting on http://127.0.0.1:9876 ...
echo  Press Ctrl+C to stop.
echo.

python "%COMPANION_DIR%companion.py"

echo.
echo  Companion stopped.
pause
