# B-Roll Scout - Companion Launcher (PowerShell)
# Called by start-companion.bat. Starts the companion server and opens browser.
# ASCII-only messages to avoid UTF-8 parsing bugs in Windows PowerShell 5.1

$CompanionDir = $PSScriptRoot
$VenvDir = Join-Path $CompanionDir ".venv"
$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
$CompanionPy = Join-Path $CompanionDir "companion.py"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  B-Roll Scout Companion" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Keep this window open while using B-Roll Scout." -ForegroundColor White
Write-Host "  To stop: close this window or press Ctrl+C." -ForegroundColor White
Write-Host ""

# --- Stop any previous instances ---
Write-Host "  Cleaning up old instances..." -ForegroundColor Gray

# Kill by window title (background processes only)
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq BRoll-OpenBrowser" 2>$null | Out-Null

# Kill orphaned processes on port 3000
$port3000 = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
if ($port3000) {
    foreach ($line in $port3000) {
        $pid = ($line -split '\s+')[-1]
        if ($pid -match '^\d+$') {
            taskkill /f /pid $pid 2>$null | Out-Null
        }
    }
}

# --- Check if setup has been run ---
if (-not (Test-Path $ActivateScript)) {
    Write-Host "  First launch detected. Running setup..." -ForegroundColor Yellow
    Write-Host ""
    $setupScript = Join-Path $CompanionDir "setup.ps1"
    if (Test-Path $setupScript) {
        & $setupScript
    } else {
        Write-Host "  ERROR: setup.ps1 not found." -ForegroundColor Red
        Write-Host "  Please run setup.bat first." -ForegroundColor Red
    }
    return
}

# --- Activate venv ---
Write-Host "  Activating Python environment..." -ForegroundColor Gray
& $ActivateScript

# --- Health check ---
$flaskCheck = python -c "import flask" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Dependencies missing. Running setup..." -ForegroundColor Yellow
    $setupScript = Join-Path $CompanionDir "setup.ps1"
    & $setupScript
    return
}

# --- Update yt-dlp ---
Write-Host "  Updating yt-dlp..." -ForegroundColor Gray
pip install --upgrade yt-dlp --quiet 2>$null
Write-Host "  OK" -ForegroundColor Green

# --- Check companion.py exists ---
if (-not (Test-Path $CompanionPy)) {
    Write-Host ""
    Write-Host "  ERROR: companion.py not found!" -ForegroundColor Red
    Write-Host "  Expected at: $CompanionPy" -ForegroundColor Red
    return
}

# --- Info ---
Write-Host ""
Write-Host "  Companion:  http://127.0.0.1:9876" -ForegroundColor White
Write-Host "  Web app:    http://localhost:3000" -ForegroundColor White
Write-Host ""

# --- Open browser after a delay ---
Write-Host "  Opening browser in 4 seconds..." -ForegroundColor Gray
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 4
    Start-Process "http://localhost:3000"
} | Out-Null

# --- Run companion in foreground ---
Write-Host "  Starting companion server..." -ForegroundColor Green
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host ""

try {
    python $CompanionPy
} catch {
    Write-Host ""
    Write-Host "  ERROR: companion.py crashed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host "  Companion server stopped." -ForegroundColor Yellow

# --- Cleanup ---
Write-Host "  Cleaning up background processes..." -ForegroundColor Gray
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" 2>$null | Out-Null
taskkill /f /fi "WINDOWTITLE eq BRoll-OpenBrowser" 2>$null | Out-Null
$port3000 = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
if ($port3000) {
    foreach ($line in $port3000) {
        $pid = ($line -split '\s+')[-1]
        if ($pid -match '^\d+$') {
            taskkill /f /pid $pid 2>$null | Out-Null
        }
    }
}
Write-Host "  Done." -ForegroundColor Green
