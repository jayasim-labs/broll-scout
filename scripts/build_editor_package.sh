#!/bin/bash
set -euo pipefail
#
# Build a self-contained editor package:
#   dist/broll-scout-editor/
#     ├── webapp/           — Next.js standalone server (no npm needed)
#     ├── companion/        — Flask companion (yt-dlp, Whisper, etc.)
#     ├── node/             — Portable Node.js for Windows (auto-downloaded)
#     ├── setup.bat         — One-click setup for editors
#     ├── start.bat         — Daily launcher (starts both + opens browser)
#     └── update.bat        — Updates yt-dlp + packages
#
# Run from the project root:
#   bash scripts/build_editor_package.sh
#
# Output: dist/broll-scout-editor.zip

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist"
PKG_DIR="$DIST_DIR/broll-scout-editor"
NODE_VERSION="22.15.0"
NODE_ZIP="node-v${NODE_VERSION}-win-x64.zip"
NODE_URL="https://nodejs.org/dist/v${NODE_VERSION}/${NODE_ZIP}"

echo "=== Building B-Roll Scout Editor Package ==="

# ---------------------------------------------------------------
# 1. Build Next.js standalone
# ---------------------------------------------------------------
echo ""
echo "[1/5] Building Next.js standalone..."
cd "$PROJECT_DIR"

# Ensure BACKEND_URL points to production API for the build
export BACKEND_URL="https://broll.jayasim.com"
npm run build

if [ ! -d ".next/standalone" ]; then
    echo "ERROR: .next/standalone not found. Is output: 'standalone' set in next.config.mjs?"
    exit 1
fi
echo "  OK: Standalone build complete"

# ---------------------------------------------------------------
# 2. Assemble package directory
# ---------------------------------------------------------------
echo ""
echo "[2/5] Assembling package..."
rm -rf "$PKG_DIR"
mkdir -p "$PKG_DIR/webapp" "$PKG_DIR/companion"

# Copy standalone server
cp -r .next/standalone/* "$PKG_DIR/webapp/"

# Copy static assets (Next.js standalone doesn't include these)
if [ -d ".next/static" ]; then
    mkdir -p "$PKG_DIR/webapp/.next/static"
    cp -r .next/static/* "$PKG_DIR/webapp/.next/static/"
fi

# Copy public folder
if [ -d "public" ]; then
    mkdir -p "$PKG_DIR/webapp/public"
    cp -r public/* "$PKG_DIR/webapp/public/"
fi

# Create .env for the standalone server
cat > "$PKG_DIR/webapp/.env" <<'ENVEOF'
BACKEND_URL=https://broll.jayasim.com
PORT=3000
HOSTNAME=127.0.0.1
ENVEOF

# Copy companion files
cp "$PROJECT_DIR/broll-companion/companion.py"             "$PKG_DIR/companion/"
cp "$PROJECT_DIR/broll-companion/requirements.txt"         "$PKG_DIR/companion/"
echo "  OK: Package assembled"

# ---------------------------------------------------------------
# 3. Download portable Node.js for Windows
# ---------------------------------------------------------------
echo ""
echo "[3/5] Downloading portable Node.js $NODE_VERSION for Windows..."

NODE_CACHE="$DIST_DIR/.node-cache/$NODE_ZIP"
mkdir -p "$(dirname "$NODE_CACHE")"

if [ -f "$NODE_CACHE" ]; then
    echo "  Using cached: $NODE_CACHE"
else
    curl -fSL "$NODE_URL" -o "$NODE_CACHE"
    echo "  Downloaded: $NODE_URL"
fi

# Extract just node.exe
mkdir -p "$PKG_DIR/node"
cd "$DIST_DIR/.node-cache"
unzip -o -q "$NODE_ZIP" "node-v${NODE_VERSION}-win-x64/node.exe" -d "$DIST_DIR/.node-cache/" 2>/dev/null || true
cp "$DIST_DIR/.node-cache/node-v${NODE_VERSION}-win-x64/node.exe" "$PKG_DIR/node/node.exe"
echo "  OK: node.exe extracted"

# ---------------------------------------------------------------
# 4. Create batch files
# ---------------------------------------------------------------
echo ""
echo "[4/5] Creating batch files..."
cd "$PROJECT_DIR"

# ---------- setup.bat ----------
cat > "$PKG_DIR/setup.bat" <<'BATEOF'
@echo off
setlocal enabledelayedexpansion
title B-Roll Scout — Editor Setup
color 0A

echo.
echo  ============================================================
echo   B-Roll Scout — Editor Setup (One-Click)
echo  ============================================================
echo.
echo  This will set up everything you need:
echo    1. Python (auto-install if missing)
echo    2. ffmpeg (for audio processing)
echo    3. yt-dlp, Whisper AI, Flask
echo    4. Desktop shortcut
echo.
echo  The web app and Node.js are already bundled — no extra install.
echo  Estimated time: 3-5 minutes on first run.
echo  Press Ctrl+C to cancel, or
pause

set "ROOT=%~dp0"
set "COMPANION=%ROOT%companion"
set "VENV=%COMPANION%\.venv"
set PYTHON=

:: --- Find or install Python ---
echo.
echo  [1/4] Checking for Python...

python --version >nul 2>&1
if %ERRORLEVEL% equ 0 ( set PYTHON=python& goto :py_ok )
py --version >nul 2>&1
if %ERRORLEVEL% equ 0 ( set PYTHON=py& goto :py_ok )

echo  Python not found. Installing via winget...
winget --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
    for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USER_PATH=%%B"
    for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%B"
    set "PATH=!USER_PATH!;!SYS_PATH!"
    python --version >nul 2>&1 && ( set PYTHON=python& goto :py_ok )
    py --version >nul 2>&1 && ( set PYTHON=py& goto :py_ok )
    echo  Python installed but not in PATH yet. Close this window and re-run setup.bat.
    pause
    exit /b 0
) else (
    echo  Cannot auto-install Python. Download from https://www.python.org/downloads/
    echo  IMPORTANT: Check "Add Python to PATH" during install, then re-run setup.bat.
    pause
    exit /b 1
)

:py_ok
for /f "tokens=*" %%i in ('%PYTHON% --version 2^>^&1') do echo  OK: %%i

:: --- ffmpeg ---
echo.
echo  [2/4] Checking ffmpeg...
ffmpeg -version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo  OK: ffmpeg installed
) else (
    winget --version >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        winget install --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent 2>nul
        echo  OK: ffmpeg installed
    ) else (
        echo  WARNING: ffmpeg not found. Whisper will not work until you install it.
    )
)

:: --- Python venv + deps ---
echo.
echo  [3/4] Installing companion packages...
if not exist "%VENV%\Scripts\activate.bat" (
    %PYTHON% -m venv "%VENV%"
)
call "%VENV%\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet 2>nul
pip install flask flask-cors yt-dlp youtube-transcript-api --quiet
echo  OK: Core packages
pip install openai-whisper --quiet 2>nul
if %ERRORLEVEL% equ 0 (
    echo  OK: Whisper installed
    python -c "import whisper; whisper.load_model('base')" 2>nul
) else (
    echo  NOTE: Whisper install failed (optional).
)

:: --- Desktop shortcut ---
echo.
echo  [4/4] Creating desktop shortcut...
set "SHORTCUT_VBS=%TEMP%\broll_sc.vbs"
echo Set oWS = WScript.CreateObject("WScript.Shell") > "%SHORTCUT_VBS%"
echo sLinkFile = "%USERPROFILE%\Desktop\B-Roll Scout.lnk" >> "%SHORTCUT_VBS%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%SHORTCUT_VBS%"
echo oLink.TargetPath = "%ROOT%start.bat" >> "%SHORTCUT_VBS%"
echo oLink.WorkingDirectory = "%ROOT%" >> "%SHORTCUT_VBS%"
echo oLink.Description = "Start B-Roll Scout" >> "%SHORTCUT_VBS%"
echo oLink.Save >> "%SHORTCUT_VBS%"
cscript //nologo "%SHORTCUT_VBS%" 2>nul
del "%SHORTCUT_VBS%" 2>nul
echo  OK: "B-Roll Scout" shortcut on Desktop

echo.
echo  ============================================================
echo   Setup complete!
echo  ============================================================
echo.

if /i "%~1"=="/nolaunch" (
    pause
    exit /b 0
)

echo  Starting B-Roll Scout now...
echo.
start "" "%ROOT%start.bat"

echo  This setup window can be closed.
echo.
pause
BATEOF

# ---------- start.bat ----------
cat > "$PKG_DIR/start.bat" <<'BATEOF'
@echo off
title B-Roll Scout
color 0A

echo.
echo  B-Roll Scout
echo  ============
echo.

set "ROOT=%~dp0"
set "COMPANION=%ROOT%companion"
set "VENV=%COMPANION%\.venv"
set "NODE=%ROOT%node\node.exe"
set "SERVER=%ROOT%webapp\server.js"
set "PIDFILE=%ROOT%.broll-pids"

:: -----------------------------------------------------------
:: Kill any previous instances first (prevents duplicates)
:: -----------------------------------------------------------
call "%ROOT%stop.bat" /quiet 2>nul

:: Check first-time setup
if not exist "%VENV%\Scripts\activate.bat" (
    echo  First launch detected. Running setup...
    call "%ROOT%setup.bat" /nolaunch
)

call "%VENV%\Scripts\activate.bat"

:: Auto-update yt-dlp
echo  Updating yt-dlp...
pip install --upgrade yt-dlp --quiet 2>nul
echo  OK
echo.

:: -----------------------------------------------------------
:: Start the Next.js web app in background (port 3000)
:: -----------------------------------------------------------
echo  Starting web app on http://localhost:3000 ...
set PORT=3000
set HOSTNAME=127.0.0.1
start /min "BRoll-WebApp" "%NODE%" "%SERVER%"

:: Give it a moment to bind, then grab its PID
timeout /t 2 /nobreak >nul
for /f "tokens=2" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":3000 " 2^>nul') do (
    for /f "tokens=5" %%q in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":3000 " 2^>nul') do (
        echo %%q> "%PIDFILE%"
    )
)

:: Open browser after the server is ready
start /min "" cmd /c "timeout /t 4 /nobreak >nul && start "" http://localhost:3000"

:: -----------------------------------------------------------
:: Start companion in foreground (port 9876)
:: -----------------------------------------------------------
echo  Starting companion on http://127.0.0.1:9876 ...
echo.
echo  Your browser will open to http://localhost:3000 in a few seconds.
echo.
echo  ============================================================
echo   To STOP: close this window, or press Ctrl+C
echo  ============================================================
echo.

python "%COMPANION%\companion.py"

:: -----------------------------------------------------------
:: Companion exited (Ctrl+C or window closed) -- clean up
:: -----------------------------------------------------------
call "%ROOT%stop.bat" /quiet 2>nul
echo.
echo  B-Roll Scout stopped.
pause
BATEOF

# ---------- stop.bat ----------
cat > "$PKG_DIR/stop.bat" <<'BATEOF'
@echo off
:: Kills all B-Roll Scout background processes.
:: Called automatically by start.bat on launch and exit.
:: Editors can also double-click this to force-stop everything.

set "ROOT=%~dp0"
set "NODE=%ROOT%node\node.exe"
set "PIDFILE=%ROOT%.broll-pids"
set QUIET=0
if /i "%~1"=="/quiet" set QUIET=1

if %QUIET%==0 (
    echo.
    echo  Stopping B-Roll Scout...
    echo.
)

:: Kill Node.js web app (port 3000)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":3000 " 2^>nul') do (
    taskkill /f /pid %%p >nul 2>&1
    if %QUIET%==0 echo  Stopped web app (PID %%p)
)

:: Kill companion Flask (port 9876)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":9876 " 2^>nul') do (
    taskkill /f /pid %%p >nul 2>&1
    if %QUIET%==0 echo  Stopped companion (PID %%p)
)

:: Fallback: kill by window title
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" >nul 2>&1

:: Clean up PID file
del "%PIDFILE%" >nul 2>&1

if %QUIET%==0 (
    echo.
    echo  All B-Roll Scout processes stopped.
    pause
)
BATEOF

# ---------- update.bat ----------
cat > "$PKG_DIR/update.bat" <<'BATEOF'
@echo off
title B-Roll Scout — Update
color 0E

set "COMPANION=%~dp0companion"
set "VENV=%COMPANION%\.venv"

if not exist "%VENV%\Scripts\activate.bat" (
    echo  Run setup.bat first.
    pause
    exit /b 1
)

call "%VENV%\Scripts\activate.bat"

echo  Updating packages...
python -m pip install --upgrade pip --quiet
pip install --upgrade yt-dlp flask flask-cors youtube-transcript-api openai-whisper --quiet

echo.
echo  Update complete!
pause
BATEOF

echo "  OK: Batch files created"

# ---------------------------------------------------------------
# 5. Zip it up
# ---------------------------------------------------------------
echo ""
echo "[5/5] Creating zip..."
cd "$DIST_DIR"
rm -f broll-scout-editor.zip
zip -r -q broll-scout-editor.zip broll-scout-editor/
echo "  OK: $DIST_DIR/broll-scout-editor.zip"

echo ""
echo "=== Done ==="
echo ""
echo "Package: $DIST_DIR/broll-scout-editor.zip"
echo ""
echo "Give this zip to editors. They:"
echo "  1. Unzip anywhere"
echo "  2. Double-click setup.bat (first time only)"
echo "  3. Double-click 'B-Roll Scout' on their Desktop (daily)"
echo ""
echo "Web app: http://localhost:3000"
echo "Companion: http://127.0.0.1:9876"
echo "API backend: https://broll.jayasim.com"
