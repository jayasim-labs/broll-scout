@echo off
title B-Roll Scout Companion
color 0A
cd /d "%~dp0"

echo.
echo  B-Roll Scout Companion
echo  ----------------------
echo  Keep this window open while using the web app.
echo.
echo  ============================================================
echo   To STOP: close this window, or press Ctrl+C
echo  ============================================================
echo.

if not exist ".venv\Scripts\activate.bat" (
    echo  ERROR: Run setup.bat first.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo  Checking for yt-dlp updates...
pip install --upgrade yt-dlp --quiet 2>nul
echo  OK
echo.
echo  Starting on http://127.0.0.1:9876
echo.

python companion.py

:: Clean up when companion exits
call "%~dp0stop.bat" /quiet 2>nul

echo.
echo  Companion stopped.
pause
