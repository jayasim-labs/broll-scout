# B-Roll Scout - Smart setup + launcher for Windows editors
# First run:  installs prerequisites (~20 min), then launches
# Every run:  skips what's already installed (~5 sec), then launches
# Called by setup.bat (double-click to run).
# ASCII-only to avoid UTF-8 parsing bugs in Windows PowerShell 5.1

$ErrorActionPreference = "Stop"
$CompanionDir = $PSScriptRoot
$ProjectRoot = (Resolve-Path (Join-Path $CompanionDir "..")).Path
$VenvDir = Join-Path $CompanionDir ".venv"
$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
$CompanionPy = Join-Path $CompanionDir "companion.py"

$firstRun = $false

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  B-Roll Scout" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Cleanup old instances ---
Write-Host "  Cleaning up old instances..." -ForegroundColor Gray
$port3000 = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
if ($port3000) {
    foreach ($line in $port3000) {
        $pid = ($line -split '\s+')[-1]
        if ($pid -match '^\d+$') { taskkill /f /pid $pid 2>$null | Out-Null }
    }
}
$port9876 = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":9876 "
if ($port9876) {
    foreach ($line in $port9876) {
        $pid = ($line -split '\s+')[-1]
        if ($pid -match '^\d+$') { taskkill /f /pid $pid 2>$null | Out-Null }
    }
}

# ===================================================================
# PREREQUISITES - each section skips if already satisfied
# ===================================================================

# --- 1. Node.js ---
Write-Host "[1/8] Node.js..." -ForegroundColor Yellow

$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Write-Host "  Node.js is not installed." -ForegroundColor Red
    Write-Host "  Please install Node.js 20+ from: https://nodejs.org/" -ForegroundColor White
    Write-Host "  Then run setup.bat again." -ForegroundColor White
    exit 1
}
$nodeVer = (node -v) -replace 'v', ''
$major = [int]($nodeVer.Split('.')[0])
if ($major -lt 18) {
    Write-Host "  Node.js $nodeVer found. Node 18+ is recommended." -ForegroundColor Yellow
} else {
    Write-Host "  OK - Node.js $nodeVer" -ForegroundColor Green
}

# --- 2. Python ---
Write-Host ""
Write-Host "[2/8] Python..." -ForegroundColor Yellow

$pythonCmd = $null
$pythonExe = Get-Command python -ErrorAction SilentlyContinue
if ($pythonExe) {
    $ver = & python --version 2>&1
    if ($ver -match "Python \d") {
        $pythonCmd = "python"
        Write-Host "  OK - $ver" -ForegroundColor Green
    }
}
if (-not $pythonCmd) {
    $pyExe = Get-Command py -ErrorAction SilentlyContinue
    if ($pyExe) {
        $ver = & py --version 2>&1
        if ($ver -match "Python \d") {
            $pythonCmd = "py"
            Write-Host "  OK - $ver" -ForegroundColor Green
        }
    }
}
if (-not $pythonCmd) {
    $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetCmd) {
        $firstRun = $true
        Write-Host "  Python not found. Installing via winget (1-2 min)..." -ForegroundColor Yellow
        winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
        Write-Host ""
        Write-Host "  Python installed. CLOSE this window and re-run setup.bat." -ForegroundColor Cyan
        exit 0
    }
    Write-Host "  Python not installed." -ForegroundColor Red
    Write-Host "  Download from: https://www.python.org/downloads/" -ForegroundColor White
    Write-Host "  IMPORTANT: Check 'Add Python to PATH' during install." -ForegroundColor White
    exit 1
}

# --- 3. yt-dlp and ffmpeg ---
Write-Host ""
Write-Host "[3/8] yt-dlp & ffmpeg..." -ForegroundColor Yellow

$ytdlp = Get-Command yt-dlp -ErrorAction SilentlyContinue
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue

if (-not $ytdlp -or -not $ffmpeg) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        $firstRun = $true
        Write-Host "  Installing missing tools via winget..." -ForegroundColor White
        if (-not $ytdlp) {
            winget install --id yt-dlp.yt-dlp --accept-source-agreements --accept-package-agreements 2>$null
            if ($LASTEXITCODE -ne 0) { Write-Host "  Could not install yt-dlp via winget." -ForegroundColor Yellow }
        }
        if (-not $ffmpeg) {
            winget install --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements 2>$null
            if ($LASTEXITCODE -ne 0) { Write-Host "  Could not install ffmpeg via winget." -ForegroundColor Yellow }
        }
    } else {
        if (-not $ytdlp) { Write-Host "  yt-dlp not found. Install from: https://github.com/yt-dlp/yt-dlp/releases" -ForegroundColor Yellow }
        if (-not $ffmpeg) { Write-Host "  ffmpeg not found. Install from: https://ffmpeg.org/download.html" -ForegroundColor Yellow }
    }
} else {
    Write-Host "  OK - yt-dlp and ffmpeg installed" -ForegroundColor Green
}

# --- 4. npm packages ---
Write-Host ""
Write-Host "[4/8] npm packages..." -ForegroundColor Yellow

Set-Location $ProjectRoot
$nodeModules = Join-Path $ProjectRoot "node_modules"
if (Test-Path (Join-Path $nodeModules ".package-lock.json")) {
    Write-Host "  OK - node_modules present" -ForegroundColor Green
} else {
    $firstRun = $true
    Write-Host "  Running npm install (first time)..." -ForegroundColor White
    npm install --legacy-peer-deps
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  npm install failed. Fix errors above and re-run setup.bat." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK - npm dependencies installed" -ForegroundColor Green
}

# --- 5. Environment file ---
Write-Host ""
Write-Host "[5/8] Environment (.env.local)..." -ForegroundColor Yellow

$envLocal = Join-Path $ProjectRoot ".env.local"
$envExample = Join-Path $ProjectRoot ".env.example"

if (Test-Path $envLocal) {
    Write-Host "  OK - .env.local exists" -ForegroundColor Green
} elseif (Test-Path $envExample) {
    $firstRun = $true
    Copy-Item $envExample $envLocal
    $secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    $raw = Get-Content $envLocal -Raw
    $updated = $raw -replace 'SESSION_SECRET=change-me-to-random-32-char-string', "SESSION_SECRET=$secret"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($envLocal, $updated, $utf8NoBom)
    Write-Host "  OK - Created .env.local" -ForegroundColor Green
} else {
    $firstRun = $true
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($envLocal, "BACKEND_URL=https://broll.jayasim.com`nBACKEND_API_KEY=zQCtPzOz1LU2rDK-vtzzcWey18yO1ZgqyU4cCloWwZE`n", $utf8NoBom)
    Write-Host "  OK - Created minimal .env.local" -ForegroundColor Green
}

# Ensure BACKEND_URL points to production
$envContent = Get-Content $envLocal -Raw
if ($envContent -match 'BACKEND_URL=http://localhost') {
    $envContent = $envContent -replace 'BACKEND_URL=http://localhost:\d+', 'BACKEND_URL=https://broll.jayasim.com'
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($envLocal, $envContent, $utf8NoBom)
    Write-Host "  Fixed BACKEND_URL -> https://broll.jayasim.com" -ForegroundColor Cyan
} elseif ($envContent -notmatch 'BACKEND_URL=') {
    Add-Content $envLocal "`nBACKEND_URL=https://broll.jayasim.com"
}
$envContent = Get-Content $envLocal -Raw
if ($envContent -notmatch 'BACKEND_API_KEY=') {
    Add-Content $envLocal "`nBACKEND_API_KEY=zQCtPzOz1LU2rDK-vtzzcWey18yO1ZgqyU4cCloWwZE"
}

# --- 6. Python companion venv + packages ---
Write-Host ""
Write-Host "[6/8] Python companion..." -ForegroundColor Yellow

if (Test-Path $ActivateScript) {
    & $ActivateScript
    $flaskCheck = python -c "import flask" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK - Virtual environment ready" -ForegroundColor Green
    } else {
        Write-Host "  Dependencies missing. Reinstalling..." -ForegroundColor Yellow
        & python -m pip install flask flask-cors yt-dlp youtube-transcript-api ollama --quiet
        Write-Host "  OK - Companion packages reinstalled" -ForegroundColor Green
    }
} else {
    $firstRun = $true
    Write-Host "  Creating virtual environment..." -ForegroundColor White
    & $pythonCmd -m venv $VenvDir
    if (-not (Test-Path $ActivateScript)) {
        Write-Host "  ERROR: Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
    & $ActivateScript
    Write-Host "  Upgrading pip..." -ForegroundColor White
    & python -m pip install --upgrade pip --quiet 2>$null
    Write-Host "  Installing companion packages..." -ForegroundColor White
    & python -m pip install flask flask-cors yt-dlp youtube-transcript-api ollama --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Companion package installation failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK - Companion packages installed" -ForegroundColor Green

    Write-Host "  Installing Whisper AI (speech-to-text)..." -ForegroundColor White
    # Install CUDA-enabled PyTorch first if NVIDIA GPU is available
    $hasNvidia = $false
    try {
        $nvidiaOut = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null
        if ($LASTEXITCODE -eq 0 -and $nvidiaOut) {
            $hasNvidia = $true
            Write-Host "  NVIDIA GPU detected: $($nvidiaOut.Trim()) — installing CUDA PyTorch..." -ForegroundColor Cyan
            & python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 --quiet 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  OK - PyTorch with CUDA installed (GPU-accelerated Whisper)" -ForegroundColor Green
            } else {
                Write-Host "  NOTE: CUDA PyTorch install failed, Whisper will use CPU" -ForegroundColor Yellow
            }
        }
    } catch { }

    & python -m pip install openai-whisper --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  NOTE: Whisper install failed (optional)." -ForegroundColor Yellow
    } else {
        if ($hasNvidia) {
            Write-Host "  OK - Whisper installed (CUDA GPU accelerated)" -ForegroundColor Green
        } else {
            Write-Host "  OK - Whisper installed (CPU mode)" -ForegroundColor Green
        }
    }
}

# Update yt-dlp (quick, always do this)
Write-Host "  Updating yt-dlp..." -ForegroundColor Gray
& python -m pip install --upgrade yt-dlp --quiet 2>$null
Write-Host "  OK - yt-dlp up to date" -ForegroundColor Green

# --- 7. Ollama ---
Write-Host ""
Write-Host "[7/8] Ollama..." -ForegroundColor Yellow

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    $firstRun = $true
    Write-Host "  Ollama not found. Downloading installer..." -ForegroundColor White
    $ollamaInstaller = Join-Path $env:TEMP "ollama-setup.exe"
    try {
        Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaInstaller -TimeoutSec 120
        Write-Host "  Running Ollama installer..." -ForegroundColor White
        Write-Host "  If an Ollama window opens, you can close it - setup will continue." -ForegroundColor Gray
        $installerProc = Start-Process -FilePath $ollamaInstaller -ArgumentList "/S" -PassThru -ErrorAction Stop
        $waited = 0
        while (-not $installerProc.HasExited -and $waited -lt 90) {
            Start-Sleep -Seconds 2; $waited += 2
            $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
            if (Test-Path $ollamaExe) { break }
        }
        Get-Process -Name "Ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Remove-Item $ollamaInstaller -ErrorAction SilentlyContinue
        $env:PATH = "$env:LOCALAPPDATA\Programs\Ollama;$env:PATH"
        $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
        if ($ollamaCmd) {
            Write-Host "  OK - Ollama installed" -ForegroundColor Green
        } else {
            Write-Host "  NOTE: Ollama installed but not on PATH yet. Restart terminal after setup." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  Could not install Ollama automatically: $_" -ForegroundColor Yellow
        Write-Host "  Install manually from: https://ollama.com/download" -ForegroundColor White
    }
} else {
    Write-Host "  OK - Ollama installed" -ForegroundColor Green
}

# Ensure Ollama >= 0.20.0 (required for Gemma 4)
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
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
            } else {
                Write-Host "  OK - Ollama $currentVer (Gemma 4 compatible)" -ForegroundColor Green
            }
        }
    } catch {}
}

# Start Ollama server with parallel=3
$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
    Write-Host "  Starting Ollama (OLLAMA_NUM_PARALLEL=3)..." -ForegroundColor Gray
    Get-Process -Name "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    $env:OLLAMA_NUM_PARALLEL = "3"
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden

    $ollamaRunning = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        try {
            $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2
            Write-Host "  OK - Ollama running (parallel=3)" -ForegroundColor Green
            $ollamaRunning = $true
            break
        } catch {}
    }

    # Pull models if missing (skips if already pulled)
    if ($ollamaRunning) {
        $installed = & ollama list 2>$null | Out-String
        if ($installed -match "qwen3:8b") {
            Write-Host "  OK - Qwen3 8B ready" -ForegroundColor Green
        } else {
            $firstRun = $true
            Write-Host "  Pulling Qwen3 8B model (~5GB, one-time download)..." -ForegroundColor White
            & ollama pull qwen3:8b
            if ($LASTEXITCODE -eq 0) { Write-Host "  OK - Qwen3 8B model ready" -ForegroundColor Green }
            else { Write-Host "  NOTE: Qwen3 pull failed. Run 'ollama pull qwen3:8b' later." -ForegroundColor Yellow }
        }

        if ($installed -match "gemma4:26b") {
            Write-Host "  OK - Gemma 4 26B ready" -ForegroundColor Green
        } else {
            $firstRun = $true
            Write-Host "  Pulling Gemma 4 26B MoE model (~18GB, one-time download)..." -ForegroundColor White
            Write-Host "  This may take 10-20 minutes on first run." -ForegroundColor Gray
            & ollama pull gemma4:26b
            if ($LASTEXITCODE -eq 0) { Write-Host "  OK - Gemma 4 26B MoE model ready" -ForegroundColor Green }
            else { Write-Host "  NOTE: Gemma 4 pull failed. Pull from Settings or run: ollama pull gemma4:26b" -ForegroundColor Yellow }
        }

        Write-Host "  Other models (Gemma 4 E4B, Llama 3.3 8B) can be pulled from the Settings page." -ForegroundColor Cyan
    }
} else {
    Write-Host "  WARNING: Ollama not found - install from https://ollama.com" -ForegroundColor Yellow
}

# --- 8. Desktop shortcut ---
Write-Host ""
Write-Host "[8/8] Desktop shortcut..." -ForegroundColor Yellow

$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "B-Roll Scout.lnk"
if (-not (Test-Path $shortcutPath) -or $firstRun) {
    try {
        $WshShell = New-Object -ComObject WScript.Shell
        $shortcut = $WshShell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = Join-Path $CompanionDir "setup.bat"
        $shortcut.WorkingDirectory = $CompanionDir
        $shortcut.Description = "Start B-Roll Scout"
        $shortcut.Save()
        Write-Host "  OK - 'B-Roll Scout' shortcut on Desktop" -ForegroundColor Green
    } catch {
        Write-Host "  Could not create shortcut: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "  OK - Desktop shortcut exists" -ForegroundColor Green
}

# Cookie extraction
if (-not $env:BROLL_COOKIE_BROWSER) {
    $env:BROLL_COOKIE_BROWSER = "chrome"
}

# ===================================================================
# LAUNCH
# ===================================================================

Write-Host ""
if ($firstRun) {
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Setup complete! Launching..." -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
} else {
    Write-Host "  All checks passed." -ForegroundColor Green
}
Write-Host ""

# --- Start Next.js dev server in background ---
Write-Host "  Starting web app on http://localhost:3000 ..." -ForegroundColor White

$nextBin = Join-Path $ProjectRoot "node_modules\.bin\next.cmd"
if (-not (Test-Path $nextBin)) {
    Write-Host "  next.cmd not found. Running npm install..." -ForegroundColor Yellow
    Set-Location $ProjectRoot
    npm install --legacy-peer-deps
}

$cmdArgs = '/c cd /d "' + $ProjectRoot + '" & npx next dev'
$npmJob = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdArgs -WindowStyle Minimized -PassThru

Write-Host "  Waiting for web app to start..." -ForegroundColor Gray
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $check = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
    if ($check) { $ready = $true; break }
}

if ($ready) {
    Write-Host "  OK - Web app running on http://localhost:3000" -ForegroundColor Green
    Start-Process "http://localhost:3000"
} else {
    Write-Host "  WARNING: Web app may still be starting. Check the minimized window." -ForegroundColor Yellow
}

# --- Info ---
Write-Host ""
Write-Host "  Companion:  http://127.0.0.1:9876" -ForegroundColor White
Write-Host "  Web app:    http://localhost:3000" -ForegroundColor White
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host "  Keep this window open. Press Ctrl+C to stop." -ForegroundColor White
Write-Host ""
Write-Host "  Starting companion server..." -ForegroundColor Green
Write-Host ""

# --- Run companion in foreground ---
if (-not (Test-Path $CompanionPy)) {
    Write-Host "  ERROR: companion.py not found at $CompanionPy" -ForegroundColor Red
    exit 1
}

try {
    python $CompanionPy
} catch {
    Write-Host ""
    $errMsg = $_.ToString()
    Write-Host "  ERROR: companion.py crashed: $errMsg" -ForegroundColor Red
}

# --- Cleanup ---
Write-Host ""
Write-Host "  ----------------------------------------" -ForegroundColor Gray
Write-Host "  Companion stopped. Cleaning up..." -ForegroundColor Yellow

# Kill Next.js dev server
if ($npmJob -and -not $npmJob.HasExited) {
    taskkill /f /t /pid $npmJob.Id 2>$null | Out-Null
}
$port3000 = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":3000 "
if ($port3000) {
    foreach ($line in $port3000) {
        $pid = ($line -split '\s+')[-1]
        if ($pid -match '^\d+$') { taskkill /f /pid $pid 2>$null | Out-Null }
    }
}
# Kill companion if still running
$port9876 = netstat -ano 2>$null | Select-String "LISTENING" | Select-String ":9876 "
if ($port9876) {
    foreach ($line in $port9876) {
        $pid = ($line -split '\s+')[-1]
        if ($pid -match '^\d+$') { taskkill /f /pid $pid 2>$null | Out-Null }
    }
}
# Stop Ollama server
Get-Process -Name "ollama" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

Write-Host "  Done. All processes stopped." -ForegroundColor Green
