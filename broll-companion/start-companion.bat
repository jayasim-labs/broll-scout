@echo off
title B-Roll Scout Companion
color 0A

echo.
echo  B-Roll Scout Companion
echo  ──────────────────────
echo  Keep this window open while using broll.jayasim.com
echo.

set VENV_DIR=%~dp0.venv

:: Check if venv exists
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  ERROR: Virtual environment not found.
    echo  Run install.bat first to set up the companion app.
    echo.
    pause
    exit /b 1
)

:: Activate venv
call "%VENV_DIR%\Scripts\activate.bat"

:: Quick dependency check
python -c "import flask" 2>nul
if %ERRORLEVEL% neq 0 (
    echo  ERROR: Flask not installed. Run install.bat first.
    pause
    exit /b 1
)

yt-dlp --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  WARNING: yt-dlp not found in PATH. Searches may fail.
    echo  Fix: pip install yt-dlp
    echo.
)

ffmpeg -version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  WARNING: ffmpeg not found. Whisper transcription will not work.
    echo  Fix: winget install Gyan.FFmpeg
    echo.
)

echo  Starting on http://127.0.0.1:9876 ...
echo  Press Ctrl+C to stop.
echo.

python "%~dp0companion.py"

echo.
echo  Companion stopped.
pause
