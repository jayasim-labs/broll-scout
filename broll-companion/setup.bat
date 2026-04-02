@echo off
setlocal enabledelayedexpansion
title B-Roll Scout — One-Click Setup
color 0A

echo.
echo  ============================================================
echo   B-Roll Scout — Editor Setup (One-Click)
echo  ============================================================
echo.
echo  This will set up everything you need:
echo    1. Python (auto-install if missing)
echo    2. ffmpeg (for audio processing)
echo    3. yt-dlp (YouTube search + download)
echo    4. Whisper AI (speech-to-text, 77 MB model)
echo    5. Desktop shortcut to launch the companion
echo    6. Start the companion; open your web app if app.url is configured
echo.
echo  Estimated time: 3-5 minutes on first run.
echo  Press Ctrl+C to cancel, or
pause

set COMPANION_DIR=%~dp0
set VENV_DIR=%COMPANION_DIR%.venv
set PYTHON=

:: ===================================================================
:: STEP 1: Find or install Python
:: ===================================================================
echo.
echo  [1/6] Checking for Python...

python --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON=python
    goto :python_found
)

py --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON=py
    goto :python_found
)

echo  Python not found. Installing Python automatically...
echo.

:: Try winget first (built into Windows 10/11)
winget --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo  Installing Python via winget (this may take 1-2 minutes)...
    winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
    if !ERRORLEVEL! equ 0 (
        echo  Python installed! Refreshing PATH...
        :: Refresh PATH by reading from registry
        for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%B"
        for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%B"
        set "PATH=!USER_PATH!;!SYS_PATH!"

        python --version >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set PYTHON=python
            goto :python_found
        )
        py --version >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set PYTHON=py
            goto :python_found
        )

        echo.
        echo  Python was installed but isn't in PATH yet.
        echo  Please CLOSE this window and DOUBLE-CLICK setup.bat again.
        echo.
        pause
        exit /b 0
    )
)

echo.
echo  ============================================================
echo   Could not auto-install Python.
echo   Please install it manually:
echo.
echo   1. Go to https://www.python.org/downloads/
echo   2. Download Python 3.12 or newer
echo   3. IMPORTANT: Check "Add Python to PATH" during install
echo   4. After installing, double-click setup.bat again
echo  ============================================================
echo.
pause
exit /b 1

:python_found
for /f "tokens=*" %%i in ('%PYTHON% --version 2^>^&1') do set PYVER=%%i
echo  OK: %PYVER%

:: ===================================================================
:: STEP 2: Install ffmpeg
:: ===================================================================
echo.
echo  [2/6] Checking ffmpeg...

ffmpeg -version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo  OK: ffmpeg is installed
    goto :ffmpeg_done
)

echo  ffmpeg not found. Installing...

winget --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    winget install --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent
    if !ERRORLEVEL! equ 0 (
        echo  OK: ffmpeg installed
        goto :ffmpeg_done
    )
)

choco --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    choco install ffmpeg -y >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo  OK: ffmpeg installed via chocolatey
        goto :ffmpeg_done
    )
)

echo  WARNING: Could not auto-install ffmpeg.
echo  Whisper transcription needs ffmpeg. You can install it later:
echo    winget install Gyan.FFmpeg
echo  Continuing setup...

:ffmpeg_done

:: ===================================================================
:: STEP 3: Create virtual environment
:: ===================================================================
echo.
echo  [3/6] Setting up Python environment...

if exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  OK: Environment already exists
) else (
    %PYTHON% -m venv "%VENV_DIR%"
    if %ERRORLEVEL% neq 0 (
        echo  ERROR: Failed to create Python environment.
        pause
        exit /b 1
    )
    echo  OK: Environment created
)

call "%VENV_DIR%\Scripts\activate.bat"

:: ===================================================================
:: STEP 4: Install all dependencies
:: ===================================================================
echo.
echo  [4/6] Installing packages (this takes 1-3 minutes)...
echo.

python -m pip install --upgrade pip --quiet 2>nul

echo  Installing search tools...
pip install flask flask-cors yt-dlp youtube-transcript-api --quiet
if %ERRORLEVEL% neq 0 (
    echo  ERROR: Package installation failed. Check your internet connection.
    pause
    exit /b 1
)
echo  OK: Core packages installed

echo  Installing Whisper AI (speech-to-text)...
pip install openai-whisper --quiet 2>nul
if %ERRORLEVEL% neq 0 (
    echo  NOTE: Whisper install failed (optional). Videos with captions still work fine.
    echo  To install later, you may need Visual C++ Build Tools:
    echo  https://visualstudio.microsoft.com/visual-cpp-build-tools/
) else (
    echo  OK: Whisper installed
)

:: ===================================================================
:: STEP 5: Download Whisper model
:: ===================================================================
echo.
echo  [5/6] Downloading Whisper AI model (77 MB, one-time)...
python -c "import whisper; whisper.load_model('base'); print('  OK: Model downloaded')" 2>nul
if %ERRORLEVEL% neq 0 (
    echo  Skipped — will download on first use.
)

:: ===================================================================
:: STEP 6: Create desktop shortcut
:: ===================================================================
echo.
echo  [6/6] Creating desktop shortcut...

set DESKTOP=%USERPROFILE%\Desktop
set SHORTCUT_VBS=%TEMP%\broll_shortcut.vbs

echo Set oWS = WScript.CreateObject("WScript.Shell") > "%SHORTCUT_VBS%"
echo sLinkFile = "%DESKTOP%\B-Roll Scout Companion.lnk" >> "%SHORTCUT_VBS%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%SHORTCUT_VBS%"
echo oLink.TargetPath = "%COMPANION_DIR%start-companion.bat" >> "%SHORTCUT_VBS%"
echo oLink.WorkingDirectory = "%COMPANION_DIR%" >> "%SHORTCUT_VBS%"
echo oLink.Description = "Start B-Roll Scout Companion" >> "%SHORTCUT_VBS%"
echo oLink.Save >> "%SHORTCUT_VBS%"

cscript //nologo "%SHORTCUT_VBS%" 2>nul
if %ERRORLEVEL% equ 0 (
    echo  OK: "B-Roll Scout Companion" shortcut added to Desktop
) else (
    echo  Could not create shortcut. You can run start-companion.bat manually.
)
del "%SHORTCUT_VBS%" 2>nul

:: ===================================================================
:: DONE
:: ===================================================================
echo.
echo  ============================================================
echo   Setup complete!
echo  ============================================================
echo.

if /i "%~1"=="/nolaunch" (
    echo  Press any key to continue — the companion will start in this window...
    echo.
    pause
    exit /b 0
)

:: Kill any previous instances before starting
call "%COMPANION_DIR%stop.bat" /quiet 2>nul

echo  Starting the Companion app in a new window...
echo  (Keep that window open while you use B-Roll Scout.)
echo.
start "B-Roll Scout Companion" "%COMPANION_DIR%launch-companion-server.bat"

echo  Waiting a few seconds for the companion to be ready...
timeout /t 5 /nobreak >nul

call "%COMPANION_DIR%load-app-url.bat"
if not "%BROLL_WEB_URL%"=="" (
    echo  Opening your B-Roll Scout web app in the default browser...
    start "" "%BROLL_WEB_URL%"
) else (
    echo.
    echo  No app.url file found — browser was not opened.
    echo  Copy app.url.example to app.url and add your web app URL on one line.
    echo  Note: broll.jayasim.com hosts the API only; the editor UI is separate ^(e.g. Vercel^).
    echo.
)

echo.
echo  HOW TO USE NEXT TIME:
echo.
echo    - Double-click "B-Roll Scout Companion" on your Desktop ^(or start-companion.bat^).
echo    - If app.url is set, your browser opens automatically to the editor.
echo    - Keep the companion window open while scouting B-roll.
echo.
echo  This setup window can be closed when you are done reading.
echo.
pause
