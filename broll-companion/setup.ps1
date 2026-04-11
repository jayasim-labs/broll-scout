# B-Roll Scout - Full Editor Setup (PowerShell)
# Called by setup.bat. Installs BOTH the Next.js web app AND the Python companion.
# ASCII-only to avoid UTF-8 parsing bugs in Windows PowerShell 5.1

$ErrorActionPreference = "Stop"
$CompanionDir = $PSScriptRoot
$ProjectRoot = (Resolve-Path (Join-Path $CompanionDir "..")).Path
$VenvDir = Join-Path $CompanionDir ".venv"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  B-Roll Scout - Editor Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. Check Node.js ---
Write-Host "[1/8] Checking Node.js..." -ForegroundColor Yellow

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

# --- 2. Check Python ---
Write-Host ""
Write-Host "[2/8] Checking Python..." -ForegroundColor Yellow

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

# --- 3. Check yt-dlp and ffmpeg ---
Write-Host ""
Write-Host "[3/8] Checking yt-dlp and ffmpeg..." -ForegroundColor Yellow

$ytdlp = Get-Command yt-dlp -ErrorAction SilentlyContinue
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue

if (-not $ytdlp -or -not $ffmpeg) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
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
    Write-Host "  OK - yt-dlp and ffmpeg are installed" -ForegroundColor Green
}

# --- 4. npm install ---
Write-Host ""
Write-Host "[4/8] Installing npm dependencies..." -ForegroundColor Yellow

Set-Location $ProjectRoot
npm install --legacy-peer-deps
if ($LASTEXITCODE -ne 0) {
    Write-Host "  npm install failed. Fix errors above and re-run setup.bat." -ForegroundColor Red
    exit 1
}
Write-Host "  OK - npm dependencies installed" -ForegroundColor Green

# --- 5. Environment file ---
Write-Host ""
Write-Host "[5/8] Setting up environment (.env.local)..." -ForegroundColor Yellow

$envLocal = Join-Path $ProjectRoot ".env.local"
$envExample = Join-Path $ProjectRoot ".env.example"

if (Test-Path $envLocal) {
    Write-Host "  .env.local already exists (keeping your keys)" -ForegroundColor Green
} elseif (Test-Path $envExample) {
    Copy-Item $envExample $envLocal
    $secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
    $raw = Get-Content $envLocal -Raw
    $updated = $raw -replace 'SESSION_SECRET=change-me-to-random-32-char-string', "SESSION_SECRET=$secret"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($envLocal, $updated, $utf8NoBom)
    Write-Host "  OK - Created .env.local" -ForegroundColor Green
} else {
    Write-Host "  .env.example not found. Creating minimal .env.local..." -ForegroundColor Yellow
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($envLocal, "BACKEND_URL=https://broll.jayasim.com`nBACKEND_API_KEY=zQCtPzOz1LU2rDK-vtzzcWey18yO1ZgqyU4cCloWwZE`n", $utf8NoBom)
}

# Always ensure BACKEND_URL points to production API
$envContent = Get-Content $envLocal -Raw
if ($envContent -match 'BACKEND_URL=http://localhost') {
    $envContent = $envContent -replace 'BACKEND_URL=http://localhost:\d+', 'BACKEND_URL=https://broll.jayasim.com'
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($envLocal, $envContent, $utf8NoBom)
    Write-Host "  Fixed BACKEND_URL -> https://broll.jayasim.com" -ForegroundColor Cyan
} elseif ($envContent -notmatch 'BACKEND_URL=') {
    Add-Content $envLocal "`nBACKEND_URL=https://broll.jayasim.com"
    Write-Host "  Added BACKEND_URL=https://broll.jayasim.com" -ForegroundColor Cyan
}
# Ensure BACKEND_API_KEY is present
$envContent = Get-Content $envLocal -Raw
if ($envContent -notmatch 'BACKEND_API_KEY=') {
    Add-Content $envLocal "`nBACKEND_API_KEY=zQCtPzOz1LU2rDK-vtzzcWey18yO1ZgqyU4cCloWwZE"
    Write-Host "  Added BACKEND_API_KEY" -ForegroundColor Cyan
}
Write-Host "  OK - Backend: broll.jayasim.com" -ForegroundColor Green

# --- 6. Python companion venv + packages ---
Write-Host ""
Write-Host "[6/8] Setting up Python companion..." -ForegroundColor Yellow

$activateScript = Join-Path $VenvDir "Scripts\Activate.ps1"

if (Test-Path $activateScript) {
    Write-Host "  OK - Virtual environment already exists" -ForegroundColor Green
} else {
    Write-Host "  Creating virtual environment..." -ForegroundColor White
    & $pythonCmd -m venv $VenvDir
    if (-not (Test-Path $activateScript)) {
        Write-Host "  ERROR: Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK - Virtual environment created" -ForegroundColor Green
}

Write-Host "  Activating environment..." -ForegroundColor White
& $activateScript

Write-Host "  Upgrading pip..." -ForegroundColor White
& python -m pip install --upgrade pip --quiet 2>$null

Write-Host "  Installing flask, flask-cors, yt-dlp, youtube-transcript-api, ollama..." -ForegroundColor White
& python -m pip install flask flask-cors yt-dlp youtube-transcript-api ollama --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: Companion package installation failed." -ForegroundColor Red
    exit 1
}
Write-Host "  OK - Companion packages installed" -ForegroundColor Green

Write-Host "  Installing Whisper AI (optional, speech-to-text)..." -ForegroundColor White
& python -m pip install openai-whisper --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  NOTE: Whisper install failed (optional)." -ForegroundColor Yellow
} else {
    Write-Host "  OK - Whisper installed" -ForegroundColor Green
    $modelFile = Join-Path $env:USERPROFILE ".cache\whisper\base.pt"
    if ((Test-Path $modelFile) -and (Get-Item $modelFile).Length -gt 70000000) {
        Write-Host "  OK - Whisper model already downloaded" -ForegroundColor Green
    } else {
        Write-Host "  Skipping model pre-download. Will download on first use." -ForegroundColor Gray
    }
}

# --- 7. Install Ollama (local LLM for timestamp matching) ---
Write-Host ""
Write-Host "[7/8] Setting up Ollama (local LLM, free timestamp matching)..." -ForegroundColor Yellow

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCmd) {
    Write-Host "  Ollama not found. Downloading installer..." -ForegroundColor White
    $ollamaInstaller = Join-Path $env:TEMP "ollama-setup.exe"
    try {
        Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaInstaller -TimeoutSec 120
        Write-Host "  Running Ollama installer (this may take a minute)..." -ForegroundColor White
        Write-Host "  If an Ollama window opens, you can close it - setup will continue." -ForegroundColor Gray
        $installerProc = Start-Process -FilePath $ollamaInstaller -ArgumentList "/S" -PassThru -ErrorAction Stop

        # Wait up to 90 seconds for the installer to finish
        $waited = 0
        while (-not $installerProc.HasExited -and $waited -lt 90) {
            Start-Sleep -Seconds 2
            $waited += 2
            # Check if ollama.exe appeared on disk (installer done even if GUI still open)
            $ollamaExe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
            if (Test-Path $ollamaExe) { break }
        }

        # Close the Ollama GUI app if it launched (it blocks the installer process)
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
        Write-Host "  Timestamp matching will use GPT-4o-mini (API) as fallback." -ForegroundColor Gray
    }
} else {
    Write-Host "  OK - Ollama already installed" -ForegroundColor Green
}

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
    # Ensure Ollama server is running before pulling
    $ollamaRunning = $false
    try { $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2; $ollamaRunning = $true } catch {}
    if (-not $ollamaRunning) {
        Write-Host "  Starting Ollama server..." -ForegroundColor Gray
        Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden -ErrorAction SilentlyContinue
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Seconds 1
            try { $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2; $ollamaRunning = $true; break } catch {}
        }
    }

    Write-Host "  Pulling Qwen3 8B model (~5GB, one-time download)..." -ForegroundColor White
    Write-Host "  This may take several minutes on first run." -ForegroundColor Gray
    & ollama pull qwen3:8b
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK - Qwen3 8B model ready" -ForegroundColor Green
    } else {
        Write-Host "  NOTE: Model pull failed. You can run 'ollama pull qwen3:8b' later." -ForegroundColor Yellow
        Write-Host "  Timestamp matching will use GPT-4o-mini (API) as fallback." -ForegroundColor Gray
    }
}

# --- 8. Desktop shortcut ---
Write-Host ""
Write-Host "  Creating desktop shortcut..." -ForegroundColor Yellow
try {
    $WshShell = New-Object -ComObject WScript.Shell
    $shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "B-Roll Scout.lnk"
    $shortcut = $WshShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $CompanionDir "start-companion.bat"
    $shortcut.WorkingDirectory = $CompanionDir
    $shortcut.Description = "Start B-Roll Scout"
    $shortcut.Save()
    Write-Host "  OK - 'B-Roll Scout' shortcut on Desktop" -ForegroundColor Green
} catch {
    Write-Host "  Could not create shortcut: $_" -ForegroundColor Yellow
}

# --- Done ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Starting B-Roll Scout now..." -ForegroundColor White
Write-Host "  Next time, double-click 'B-Roll Scout' on your Desktop." -ForegroundColor White
Write-Host ""

$startScript = Join-Path $CompanionDir "start-companion.ps1"
if (Test-Path $startScript) {
    & $startScript
} else {
    Write-Host "  ERROR: start-companion.ps1 not found at $startScript" -ForegroundColor Red
    exit 1
}
