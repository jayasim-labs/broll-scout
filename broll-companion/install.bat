@echo off
setlocal enabledelayedexpansion
title B-Roll Scout Companion — Installer
color 0A

echo.
echo  ============================================================
echo   B-Roll Scout Companion — Windows Installer
echo  ============================================================
echo.
echo  This will install everything needed for the companion app:
echo    - Python (check)
echo    - ffmpeg (via winget or choco)
echo    - yt-dlp
echo    - youtube-transcript-api
echo    - openai-whisper
echo    - Flask + dependencies
echo    - Virtual environment setup
echo.
echo  Press Ctrl+C to cancel, or
pause

:: -------------------------------------------------------------------
:: 1. Check Python
:: -------------------------------------------------------------------
echo.
echo [1/7] Checking Python...
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    py --version >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        echo.
        echo  ERROR: Python is not installed or not in PATH.
        echo  Download from: https://www.python.org/downloads/
        echo  IMPORTANT: Check "Add Python to PATH" during installation.
        echo.
        pause
        exit /b 1
    )
    set PYTHON=py
) else (
    set PYTHON=python
)

for /f "tokens=*" %%i in ('%PYTHON% --version 2^>^&1') do set PYVER=%%i
echo  OK: %PYVER%

:: -------------------------------------------------------------------
:: 2. Check/Install ffmpeg
:: -------------------------------------------------------------------
echo.
echo [2/7] Checking ffmpeg...
ffmpeg -version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  ffmpeg not found. Attempting to install...

    :: Try winget first (Windows 10 1709+ / Windows 11)
    winget --version >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo  Installing ffmpeg via winget...
        winget install --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
        if !ERRORLEVEL! equ 0 (
            echo  OK: ffmpeg installed via winget
            echo  NOTE: You may need to restart this terminal for ffmpeg to be in PATH.
        ) else (
            echo  winget install failed. Trying chocolatey...
            goto :try_choco_ffmpeg
        )
    ) else (
        :try_choco_ffmpeg
        choco --version >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            echo  Installing ffmpeg via chocolatey...
            choco install ffmpeg -y
            if !ERRORLEVEL! equ 0 (
                echo  OK: ffmpeg installed via chocolatey
            ) else (
                goto :ffmpeg_manual
            )
        ) else (
            :ffmpeg_manual
            echo.
            echo  WARNING: Could not auto-install ffmpeg.
            echo  Please install manually:
            echo    Option A: winget install Gyan.FFmpeg
            echo    Option B: choco install ffmpeg
            echo    Option C: Download from https://ffmpeg.org/download.html
            echo              and add the bin folder to your system PATH.
            echo.
            echo  Whisper transcription will NOT work without ffmpeg.
            echo  Continuing with the rest of the setup...
            echo.
        )
    )
) else (
    echo  OK: ffmpeg is installed
)

:: -------------------------------------------------------------------
:: 3. Create virtual environment
:: -------------------------------------------------------------------
echo.
echo [3/7] Creating Python virtual environment...

set VENV_DIR=%~dp0.venv

if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  Virtual environment already exists at %VENV_DIR%
) else (
    %PYTHON% -m venv "%VENV_DIR%"
    if %ERRORLEVEL% neq 0 (
        echo  ERROR: Failed to create virtual environment.
        echo  Try: %PYTHON% -m pip install --user virtualenv
        pause
        exit /b 1
    )
    echo  OK: Created virtual environment
)

:: Activate venv for the rest of the install
call "%VENV_DIR%\Scripts\activate.bat"
echo  OK: Activated virtual environment

:: -------------------------------------------------------------------
:: 4. Upgrade pip
:: -------------------------------------------------------------------
echo.
echo [4/7] Upgrading pip...
python -m pip install --upgrade pip --quiet
echo  OK: pip upgraded

:: -------------------------------------------------------------------
:: 5. Install Python dependencies
:: -------------------------------------------------------------------
echo.
echo [5/7] Installing Python packages (this may take a few minutes)...
echo  Installing: flask, flask-cors, yt-dlp, youtube-transcript-api, openai-whisper
echo.

pip install flask flask-cors --quiet
if %ERRORLEVEL% neq 0 (
    echo  ERROR: Failed to install flask/flask-cors
    pause
    exit /b 1
)
echo  OK: Flask installed

pip install yt-dlp --quiet
if %ERRORLEVEL% neq 0 (
    echo  ERROR: Failed to install yt-dlp
    pause
    exit /b 1
)
echo  OK: yt-dlp installed

pip install youtube-transcript-api --quiet
if %ERRORLEVEL% neq 0 (
    echo  ERROR: Failed to install youtube-transcript-api
    pause
    exit /b 1
)
echo  OK: youtube-transcript-api installed

echo.
echo  Installing openai-whisper (this downloads the model ~140 MB)...
pip install openai-whisper --quiet
if %ERRORLEVEL% neq 0 (
    echo.
    echo  WARNING: openai-whisper failed to install.
    echo  This is optional — the app still works without Whisper.
    echo  To fix later: pip install openai-whisper
    echo  Whisper requires: Visual C++ Build Tools from
    echo  https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo.
) else (
    echo  OK: openai-whisper installed
)

:: -------------------------------------------------------------------
:: 6. Verify installations
:: -------------------------------------------------------------------
echo.
echo [6/7] Verifying installations...
echo.

python -c "import flask; print('  Flask:', flask.__version__)" 2>nul
if %ERRORLEVEL% neq 0 echo  FAIL: Flask not found

python -c "from flask_cors import CORS; print('  flask-cors: OK')" 2>nul
if %ERRORLEVEL% neq 0 echo  FAIL: flask-cors not found

python -c "import yt_dlp; print('  yt-dlp:', yt_dlp.version.__version__)" 2>nul
if %ERRORLEVEL% neq 0 echo  FAIL: yt-dlp not found

yt-dlp --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    for /f "tokens=*" %%v in ('yt-dlp --version 2^>^&1') do echo   yt-dlp CLI: %%v
) else (
    echo   WARNING: yt-dlp CLI not in PATH. Try restarting your terminal.
)

python -c "import youtube_transcript_api; print('  youtube-transcript-api: OK')" 2>nul
if %ERRORLEVEL% neq 0 echo  FAIL: youtube-transcript-api not found

python -c "import whisper; print('  openai-whisper: OK')" 2>nul
if %ERRORLEVEL% neq 0 echo  NOTE: openai-whisper not available (optional)

ffmpeg -version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo   ffmpeg: OK
) else (
    echo   WARNING: ffmpeg not in PATH — needed for Whisper
)

:: -------------------------------------------------------------------
:: 7. Pre-download Whisper model
:: -------------------------------------------------------------------
echo.
echo [7/7] Pre-downloading Whisper base model (77 MB, one-time)...
python -c "import whisper; whisper.load_model('base'); print('  OK: Whisper base model cached')" 2>nul
if %ERRORLEVEL% neq 0 (
    echo   Skipped — Whisper not installed or download failed.
    echo   The model will download on first use if Whisper is available.
)

:: -------------------------------------------------------------------
:: Done
:: -------------------------------------------------------------------
echo.
echo  ============================================================
echo   Installation complete!
echo  ============================================================
echo.
echo  To start the companion app:
echo.
echo    Double-click:  start-companion.bat
echo    Or in terminal: cd %~dp0
echo                    .venv\Scripts\activate
echo                    python companion.py
echo.
echo  Keep the companion running while using the B-Roll Scout web app (see app.url / README)
echo  The browser will automatically detect it.
echo.
pause
