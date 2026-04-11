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
& python -m pip install --upgrade yt-dlp --quiet 2>$null
Write-Host "  OK" -ForegroundColor Green

# --- Cookie extraction (reduces YouTube rate-limiting) ---
if (-not $env:BROLL_COOKIE_BROWSER) {
    $env:BROLL_COOKIE_BROWSER = "chrome"
}

# --- Restart Ollama with OLLAMA_NUM_PARALLEL=3 ---
$ollamaPath = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaPath) {
    # Ensure Ollama >= 0.20.0 (required for Gemma 4 models)
    $minOllama = [version]"0.20.0"
    try {
        $verOutput = & ollama --version 2>&1
        if ($verOutput -match '(\d+\.\d+\.\d+)') {
            $currentVer = [version]$Matches[1]
            if ($currentVer -lt $minOllama) {
                Write-Host "  Ollama $currentVer is too old for Gemma 4 (needs $minOllama+). Updating..." -ForegroundColor Yellow
                $ollamaInstaller = Join-Path $env:TEMP "ollama-update.exe"
                try {
                    Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaInstaller -TimeoutSec 120
                    $updateProc = Start-Process -FilePath $ollamaInstaller -ArgumentList "/S" -PassThru -ErrorAction Stop
                    $waited = 0
                    while (-not $updateProc.HasExited -and $waited -lt 90) {
                        Start-Sleep -Seconds 2; $waited += 2
                        $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
                        if (Test-Path $ollamaExe) { break }
                    }
                    Get-Process -Name "Ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 1
                    Remove-Item $ollamaInstaller -ErrorAction SilentlyContinue
                    $env:PATH = "$env:LOCALAPPDATA\Programs\Ollama;$env:PATH"
                    $newVer = & ollama --version 2>&1
                    Write-Host "  OK - Ollama updated ($newVer)" -ForegroundColor Green
                } catch {
                    Write-Host "  Auto-update failed: $_" -ForegroundColor Yellow
                    Write-Host "  Update manually from: https://ollama.com/download" -ForegroundColor White
                }
            }
        }
    } catch {}

    Write-Host "  Stopping Ollama (to apply OLLAMA_NUM_PARALLEL=3)..." -ForegroundColor Gray
    Get-Process -Name "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1

    Write-Host "  Starting Ollama server (OLLAMA_NUM_PARALLEL=3)..." -ForegroundColor Gray
    $env:OLLAMA_NUM_PARALLEL = "3"
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
            Write-Host "  OK - Ollama running (parallel=3)" -ForegroundColor Green
            break
        } catch {}
    }
    # Auto-pull required models if missing
    $installed = & ollama list 2>$null | Out-String
    if ($installed -notmatch "qwen3:8b") {
        Write-Host "  Pulling Qwen3 8B (~5GB)..." -ForegroundColor Gray
        & ollama pull qwen3:8b
        if ($LASTEXITCODE -eq 0) { Write-Host "  OK - Qwen3 8B ready" -ForegroundColor Green }
        else { Write-Host "  Qwen3 pull failed" -ForegroundColor Yellow }
    }
    if ($installed -notmatch "gemma4:26b") {
        Write-Host "  Pulling Gemma 4 26B MoE (~18GB, first time only)..." -ForegroundColor Gray
        & ollama pull gemma4:26b
        if ($LASTEXITCODE -eq 0) { Write-Host "  OK - Gemma 4 26B ready" -ForegroundColor Green }
        else { Write-Host "  Gemma 4 pull failed - pull from Settings later" -ForegroundColor Yellow }
    }
} else {
    Write-Host "  WARNING: Ollama not found - install from https://ollama.com" -ForegroundColor Yellow
}

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
Write-Host "  Starting web app on http://localhost:3000 ..." -ForegroundColor White

$nextBin = Join-Path $ProjectRoot "node_modules\.bin\next.cmd"
if (-not (Test-Path $nextBin)) {
    Write-Host "  ERROR: next.cmd not found. Run npm install first." -ForegroundColor Red
    Write-Host "  Expected at: $nextBin" -ForegroundColor Red
    return
}

$cmdArgs = '/c cd /d "' + $ProjectRoot + '" & npx next dev'
$npmJob = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdArgs -WindowStyle Minimized -PassThru

# Wait for port 3000 to be ready (up to 30 seconds)
Write-Host "  Waiting for web app to start..." -ForegroundColor Gray
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $check = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
    if ($check) {
        $ready = $true
        break
    }
}

if ($ready) {
    Write-Host "  OK - Web app is running on http://localhost:3000" -ForegroundColor Green
    Start-Process "http://localhost:3000"
} else {
    Write-Host "  WARNING: Web app may still be starting. Check the minimized window." -ForegroundColor Yellow
    Write-Host "  You can manually open http://localhost:3000 in your browser." -ForegroundColor Yellow
}

# --- Info ---
Write-Host ""
Write-Host "  Companion:  http://127.0.0.1:9876" -ForegroundColor White
Write-Host "  Web app:    http://localhost:3000" -ForegroundColor White
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host ""

# --- Run companion in foreground ---
Write-Host "  Starting companion server..." -ForegroundColor Green
Write-Host ""

try {
    python $CompanionPy
} catch {
    Write-Host ""
    $errMsg = $_.ToString()
    Write-Host "  ERROR: companion.py crashed: $errMsg" -ForegroundColor Red
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
