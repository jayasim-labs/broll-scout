@echo off
title B-Roll Scout - Setup
color 0A

echo.
echo  ============================================================
echo   B-Roll Scout - Editor Setup
echo  ============================================================
echo.
echo  This will install everything needed:
echo    1. Python (auto-install if missing)
echo    2. ffmpeg (audio processing)
echo    3. yt-dlp, Whisper AI, Flask
echo    4. Desktop shortcut
echo.
echo  After setup, the companion starts automatically.
echo  Estimated time: 3-5 minutes on first run.
echo.
echo  Press Ctrl+C to cancel, or
pause

set "COMPANION_DIR=%~dp0"
set "VENV_DIR=%COMPANION_DIR%.venv"
set "PYTHON="

:: ===================================================================
:: STEP 1: Find Python
:: ===================================================================
echo.
echo  [1/5] Checking for Python...

python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    goto python_found
)

py --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py"
    goto python_found
)

echo  Python not found. Trying to install via winget...
echo.
winget --version >nul 2>&1
if errorlevel 1 goto no_winget

echo  Installing Python via winget (1-2 minutes)...
winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
if errorlevel 1 goto no_winget

echo  Python installed. You MUST close this window and re-run setup.bat
echo  so the new PATH takes effect.
echo.
pause
exit /b 0

:no_winget
echo.
echo  ============================================================
echo   Could not auto-install Python.
echo   Please install manually:
echo.
echo   1. Go to https://www.python.org/downloads/
echo   2. Download Python 3.12 or newer
echo   3. IMPORTANT: Check "Add Python to PATH" during install
echo   4. Then double-click setup.bat again
echo  ============================================================
echo.
pause
exit /b 1

:python_found
echo  OK: Python found
%PYTHON% --version

:: ===================================================================
:: STEP 2: Install ffmpeg
:: ===================================================================
echo.
echo  [2/5] Checking ffmpeg...

ffmpeg -version >nul 2>&1
if not errorlevel 1 (
    echo  OK: ffmpeg is installed
    goto ffmpeg_done
)

echo  ffmpeg not found. Trying winget...
winget --version >nul 2>&1
if errorlevel 1 goto try_choco
winget install --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent >nul 2>&1
if not errorlevel 1 (
    echo  OK: ffmpeg installed via winget
    goto ffmpeg_done
)

:try_choco
choco --version >nul 2>&1
if errorlevel 1 goto ffmpeg_warn
choco install ffmpeg -y >nul 2>&1
if not errorlevel 1 (
    echo  OK: ffmpeg installed via chocolatey
    goto ffmpeg_done
)

:ffmpeg_warn
echo  WARNING: ffmpeg not found. Whisper will not work until installed.
echo  You can install later: winget install Gyan.FFmpeg

:ffmpeg_done

:: ===================================================================
:: STEP 3: Create virtual environment
:: ===================================================================
echo.
echo  [3/5] Setting up Python environment...

if exist "%VENV_DIR%\Scripts\activate.bat" goto venv_exists

%PYTHON% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo  ERROR: Failed to create Python environment.
    echo  Make sure Python is installed correctly.
    pause
    exit /b 1
)

:venv_exists
echo  OK: Virtual environment ready

call "%VENV_DIR%\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet 2>nul

:: ===================================================================
:: STEP 4: Install packages
:: ===================================================================
echo.
echo  [4/5] Installing packages (1-3 minutes)...
echo.

pip install flask flask-cors yt-dlp youtube-transcript-api --quiet
if errorlevel 1 (
    echo  ERROR: Package installation failed. Check your internet connection.
    pause
    exit /b 1
)
echo  OK: Core packages installed

echo  Installing Whisper AI (speech-to-text)...
pip install openai-whisper --quiet 2>nul
if errorlevel 1 (
    echo  NOTE: Whisper install failed (optional). Videos with captions still work.
    goto whisper_done
)
echo  OK: Whisper installed
echo  Downloading Whisper model (77 MB, one-time)...
python -c "import whisper; whisper.load_model('base'); print('  OK: Model downloaded')" 2>nul
if errorlevel 1 echo  Skipped model download. Will download on first use.

:whisper_done

:: ===================================================================
:: STEP 5: Create desktop shortcut
:: ===================================================================
echo.
echo  [5/5] Creating desktop shortcut...

set "SHORTCUT_VBS=%TEMP%\broll_shortcut.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%SHORTCUT_VBS%"
echo sLinkFile = "%USERPROFILE%\Desktop\B-Roll Scout.lnk" >> "%SHORTCUT_VBS%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%SHORTCUT_VBS%"
echo oLink.TargetPath = "%COMPANION_DIR%start-companion.bat" >> "%SHORTCUT_VBS%"
echo oLink.WorkingDirectory = "%COMPANION_DIR%" >> "%SHORTCUT_VBS%"
echo oLink.Description = "Start B-Roll Scout" >> "%SHORTCUT_VBS%"
echo oLink.Save >> "%SHORTCUT_VBS%"
cscript //nologo "%SHORTCUT_VBS%" 2>nul
del "%SHORTCUT_VBS%" 2>nul
echo  OK: "B-Roll Scout" shortcut on Desktop

:: ===================================================================
:: DONE
:: ===================================================================
echo.
echo  ============================================================
echo   Setup complete! Starting B-Roll Scout...
echo  ============================================================
echo.
echo  Next time, just double-click "B-Roll Scout" on your Desktop.
echo.

:: Hand off to start-companion.bat in THIS window
call "%COMPANION_DIR%start-companion.bat"

:: Safety net: if start-companion.bat exited unexpectedly, keep window open
echo.
echo  Something went wrong. The window should not have reached this point.
echo  Press any key to close...
pause >nul
