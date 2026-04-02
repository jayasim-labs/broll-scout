# B-Roll Scout - Launcher (PowerShell)
# Starts the Next.js dev server (port 3000) AND the Python companion (port 9876).
# Called by start-companion.bat.

$CompanionDir = $PSScriptRoot
$ProjectRoot = (Resolve-Path (Join-Path $CompanionDir "..")).Path
$VenvDir = Join-Path $CompanionDir ".venv"
$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
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

# Kill old Next.js dev server on port 3000
$port3000 = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
if ($port3000) {
    foreach ($line in $port3000) {
        $pid = ($line -split '\s+')[-1]
        if ($pid -match '^\d+$') {
            taskkill /f /pid $pid 2>$null | Out-Null
        }
    }
}
# Kill old companion on port 9876
$port9876 = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":9876 "
if ($port9876) {
    foreach ($line in $port9876) {
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
    if (Test-Path $setupScript) { & $setupScript }
    else { Write-Host "  ERROR: setup.ps1 not found." -ForegroundColor Red }
    return
}

# --- Activate venv ---
Write-Host "  Activating Python environment..." -ForegroundColor Gray
& $ActivateScript

# --- Health check ---
$flaskCheck = python -c "import flask" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Dependencies missing. Running setup..." -ForegroundColor Yellow
    & (Join-Path $CompanionDir "setup.ps1")
    return
}

# --- Update yt-dlp ---
Write-Host "  Updating yt-dlp..." -ForegroundColor Gray
pip install --upgrade yt-dlp --quiet 2>$null
Write-Host "  OK" -ForegroundColor Green

# --- Check companion.py ---
if (-not (Test-Path $CompanionPy)) {
    Write-Host ""
    Write-Host "  ERROR: companion.py not found!" -ForegroundColor Red
    Write-Host "  Expected at: $CompanionPy" -ForegroundColor Red
    return
}

# --- Check node_modules ---
$nodeModules = Join-Path $ProjectRoot "node_modules"
if (-not (Test-Path $nodeModules)) {
    Write-Host "  node_modules not found. Running npm install..." -ForegroundColor Yellow
    Set-Location $ProjectRoot
    npm install --legacy-peer-deps
}

# --- Start Next.js dev server in background ---
Write-Host ""
Write-Host "  Starting web app (npm run dev) on http://localhost:3000 ..." -ForegroundColor White

$npmJob = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c cd /d `"$ProjectRoot`" && npm run dev" `
    -WindowStyle Minimized `
    -PassThru

Write-Host "  Web app starting (PID $($npmJob.Id))..." -ForegroundColor Gray

# --- Open browser after a delay ---
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 8
    Start-Process "http://localhost:3000"
} | Out-Null

# --- Info ---
Write-Host ""
Write-Host "  Companion:  http://127.0.0.1:9876" -ForegroundColor White
Write-Host "  Web app:    http://localhost:3000" -ForegroundColor White
Write-Host ""
Write-Host "  Browser will open in a few seconds." -ForegroundColor Gray
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host ""

# --- Run companion in foreground ---
Write-Host "  Starting companion server..." -ForegroundColor Green
Write-Host ""

try {
    python $CompanionPy
} catch {
    Write-Host ""
    Write-Host "  ERROR: companion.py crashed: $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host "  Companion stopped. Cleaning up..." -ForegroundColor Yellow

# --- Cleanup: kill the background Next.js dev server ---
if ($npmJob -and -not $npmJob.HasExited) {
    taskkill /f /t /pid $npmJob.Id 2>$null | Out-Null
}
# Also kill by port in case the process handle is stale
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
