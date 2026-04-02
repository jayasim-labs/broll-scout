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

# ---------- setup.bat (thin wrapper) ----------
cat > "$PKG_DIR/setup.bat" <<'BATEOF'
@echo off
REM B-Roll Scout - One-click setup for editors
REM Double-click this file to install everything needed.
title B-Roll Scout - Setup
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup failed. See messages above.
)
echo.
echo Press any key to close...
pause >nul
BATEOF

# ---------- setup.ps1 ----------
cat > "$PKG_DIR/setup.ps1" <<'PS1EOF'
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$CompanionDir = Join-Path $Root "companion"
$VenvDir = Join-Path $CompanionDir ".venv"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  B-Roll Scout - Editor Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. Check Python ---
Write-Host "[1/4] Checking Python..." -ForegroundColor Yellow
$pythonCmd = $null
$pythonExe = Get-Command python -ErrorAction SilentlyContinue
if ($pythonExe) {
    $ver = & python --version 2>&1
    if ($ver -match "Python \d") { $pythonCmd = "python"; Write-Host "  OK - $ver" -ForegroundColor Green }
}
if (-not $pythonCmd) {
    $pyExe = Get-Command py -ErrorAction SilentlyContinue
    if ($pyExe) {
        $ver = & py --version 2>&1
        if ($ver -match "Python \d") { $pythonCmd = "py"; Write-Host "  OK - $ver" -ForegroundColor Green }
    }
}
if (-not $pythonCmd) {
    $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetCmd) {
        Write-Host "  Python not found. Installing via winget (1-2 min)..." -ForegroundColor Yellow
        winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
        Write-Host "  Python installed. CLOSE this window and re-run setup.bat." -ForegroundColor Cyan
        exit 0
    }
    Write-Host "  Python not installed. Download from https://www.python.org/downloads/" -ForegroundColor Red
    Write-Host "  Check 'Add Python to PATH' during install, then re-run setup.bat." -ForegroundColor White
    exit 1
}

# --- 2. Check ffmpeg ---
Write-Host ""
Write-Host "[2/4] Checking ffmpeg..." -ForegroundColor Yellow
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "  OK - ffmpeg is installed" -ForegroundColor Green
} else {
    $w = Get-Command winget -ErrorAction SilentlyContinue
    if ($w) { winget install --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent 2>$null }
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Host "  OK - ffmpeg installed" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: ffmpeg not found. Whisper won't work until installed." -ForegroundColor Yellow
    }
}

# --- 3. Create venv + install packages ---
Write-Host ""
Write-Host "[3/4] Installing companion packages (1-3 min)..." -ForegroundColor Yellow
$activate = Join-Path $VenvDir "Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Host "  Creating virtual environment..." -ForegroundColor White
    & $pythonCmd -m venv $VenvDir
    if (-not (Test-Path $activate)) { Write-Host "  ERROR: venv creation failed." -ForegroundColor Red; exit 1 }
}
& $activate
python -m pip install --upgrade pip --quiet 2>$null
pip install flask flask-cors yt-dlp youtube-transcript-api --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "  ERROR: Package install failed." -ForegroundColor Red; exit 1 }
Write-Host "  OK - Core packages" -ForegroundColor Green
pip install openai-whisper --quiet 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK - Whisper installed" -ForegroundColor Green
    python -c "import whisper; whisper.load_model('base')" 2>$null
} else {
    Write-Host "  NOTE: Whisper install failed (optional)." -ForegroundColor Yellow
}

# --- 4. Desktop shortcut ---
Write-Host ""
Write-Host "[4/4] Creating desktop shortcut..." -ForegroundColor Yellow
try {
    $WshShell = New-Object -ComObject WScript.Shell
    $lnk = $WshShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "B-Roll Scout.lnk"))
    $lnk.TargetPath = Join-Path $Root "start.bat"
    $lnk.WorkingDirectory = $Root
    $lnk.Description = "Start B-Roll Scout"
    $lnk.Save()
    Write-Host "  OK - Shortcut on Desktop" -ForegroundColor Green
} catch { Write-Host "  Could not create shortcut: $_" -ForegroundColor Yellow }

# --- Done ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete! Starting B-Roll Scout..." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""

$startScript = Join-Path $Root "start.ps1"
if (Test-Path $startScript) { & $startScript }
else { Write-Host "  ERROR: start.ps1 not found." -ForegroundColor Red; exit 1 }
PS1EOF

# ---------- start.bat (thin wrapper) ----------
cat > "$PKG_DIR/start.bat" <<'BATEOF'
@echo off
REM B-Roll Scout - Daily launcher for editors
REM Double-click this file (or the Desktop shortcut) to start.
title B-Roll Scout
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
  echo.
  echo B-Roll Scout exited with an error. See messages above.
)
echo.
echo Press any key to close...
pause >nul
BATEOF

# ---------- start.ps1 ----------
cat > "$PKG_DIR/start.ps1" <<'PS1EOF'
$Root = $PSScriptRoot
$CompanionDir = Join-Path $Root "companion"
$VenvDir = Join-Path $CompanionDir ".venv"
$NodeExe = Join-Path $Root "node\node.exe"
$ServerJs = Join-Path $Root "webapp\server.js"
$CompanionPy = Join-Path $CompanionDir "companion.py"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  B-Roll Scout" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Keep this window open while using B-Roll Scout." -ForegroundColor White
Write-Host "  To stop: close this window or press Ctrl+C." -ForegroundColor White
Write-Host ""

# --- Cleanup old instances ---
Write-Host "  Cleaning up old instances..." -ForegroundColor Gray
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq BRoll-OpenBrowser" 2>$null | Out-Null
$p3k = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
if ($p3k) { foreach ($l in $p3k) { $id=($l -split '\s+')[-1]; if ($id -match '^\d+$') { taskkill /f /pid $id 2>$null | Out-Null } } }

# --- Check venv ---
$activate = Join-Path $VenvDir "Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Host "  First launch. Running setup..." -ForegroundColor Yellow
    & (Join-Path $Root "setup.ps1")
    return
}

& $activate
$fc = python -c "import flask" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Dependencies missing. Running setup..." -ForegroundColor Yellow
    & (Join-Path $Root "setup.ps1")
    return
}

# --- Update yt-dlp ---
Write-Host "  Updating yt-dlp..." -ForegroundColor Gray
pip install --upgrade yt-dlp --quiet 2>$null

# --- Start web app ---
if (-not (Test-Path $NodeExe)) { Write-Host "  ERROR: node.exe not found at $NodeExe" -ForegroundColor Red; return }
if (-not (Test-Path $ServerJs)) { Write-Host "  ERROR: server.js not found at $ServerJs" -ForegroundColor Red; return }

Write-Host "  Starting web app on http://localhost:3000 ..." -ForegroundColor White
$env:PORT = "3000"
$env:HOSTNAME = "127.0.0.1"
Start-Process -FilePath $NodeExe -ArgumentList $ServerJs -WindowStyle Minimized

# --- Open browser ---
Start-Job -ScriptBlock { Start-Sleep -Seconds 5; Start-Process "http://localhost:3000" } | Out-Null

# --- Start companion ---
if (-not (Test-Path $CompanionPy)) { Write-Host "  ERROR: companion.py not found at $CompanionPy" -ForegroundColor Red; return }

Write-Host "  Starting companion on http://127.0.0.1:9876 ..." -ForegroundColor White
Write-Host ""
Write-Host "  Companion:  http://127.0.0.1:9876" -ForegroundColor White
Write-Host "  Web app:    http://localhost:3000" -ForegroundColor White
Write-Host ""
Write-Host "  Browser will open in a few seconds." -ForegroundColor Gray
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host ""

try { python $CompanionPy }
catch { Write-Host "  ERROR: companion.py crashed: $_" -ForegroundColor Red }

Write-Host ""
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host "  Companion stopped. Cleaning up..." -ForegroundColor Yellow

# Cleanup
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" 2>$null | Out-Null
$p3k = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
if ($p3k) { foreach ($l in $p3k) { $id=($l -split '\s+')[-1]; if ($id -match '^\d+$') { taskkill /f /pid $id 2>$null | Out-Null } } }
Write-Host "  Done." -ForegroundColor Green
PS1EOF

# ---------- stop.bat ----------
cat > "$PKG_DIR/stop.bat" <<'BATEOF'
@echo off
REM B-Roll Scout - Stop all processes
title B-Roll Scout - Stop
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "taskkill /f /fi 'WINDOWTITLE eq BRoll-WebApp' 2>$null; " ^
  "taskkill /f /fi 'WINDOWTITLE eq BRoll-OpenBrowser' 2>$null; " ^
  "$p = netstat -ano 2>$null | Select-String 'LISTENING' | Select-String ':3000 '; " ^
  "if ($p) { foreach ($l in $p) { $id = ($l -split '\s+')[-1]; if ($id -match '^\d+$') { taskkill /f /pid $id 2>$null } } }; " ^
  "$p = netstat -ano 2>$null | Select-String 'LISTENING' | Select-String ':9876 '; " ^
  "if ($p) { foreach ($l in $p) { $id = ($l -split '\s+')[-1]; if ($id -match '^\d+$') { taskkill /f /pid $id 2>$null } } }; " ^
  "Write-Host '' ; Write-Host '  All B-Roll Scout processes stopped.' -ForegroundColor Green"

echo.
echo Press any key to close...
pause >nul
BATEOF

# ---------- update.bat ----------
cat > "$PKG_DIR/update.bat" <<'BATEOF'
@echo off
REM B-Roll Scout - Update packages
title B-Roll Scout - Update
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$v = Join-Path '%~dp0' 'companion\.venv\Scripts\Activate.ps1'; " ^
  "if (-not (Test-Path $v)) { Write-Host 'Run setup.bat first.' -ForegroundColor Red; exit 1 }; " ^
  "& $v; " ^
  "Write-Host 'Updating packages...' -ForegroundColor Yellow; " ^
  "python -m pip install --upgrade pip --quiet; " ^
  "pip install --upgrade yt-dlp flask flask-cors youtube-transcript-api openai-whisper --quiet; " ^
  "Write-Host 'Update complete!' -ForegroundColor Green"

echo.
echo Press any key to close...
pause >nul
BATEOF

# Convert all .bat and .ps1 files to CRLF (cmd.exe silently crashes on LF-only)
for f in "$PKG_DIR"/*.bat "$PKG_DIR"/*.ps1; do
    [ -f "$f" ] && perl -pi -e 's/\r?\n/\r\n/' "$f"
done
echo "  OK: Batch + PowerShell files created (CRLF)"

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
