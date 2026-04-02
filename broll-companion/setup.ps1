# B-Roll Scout - Companion Setup (PowerShell)
# Called by setup.bat. Do not run directly unless you know what you are doing.
# ASCII-only messages to avoid UTF-8 parsing bugs in Windows PowerShell 5.1

$ErrorActionPreference = "Stop"
$CompanionDir = $PSScriptRoot
$VenvDir = Join-Path $CompanionDir ".venv"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  B-Roll Scout - Editor Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- 1. Check Python ---
Write-Host "[1/5] Checking Python..." -ForegroundColor Yellow

$pythonCmd = $null
$pythonExe = Get-Command python -ErrorAction SilentlyContinue
if ($pythonExe) {
    $ver = & python --version 2>&1
    # Make sure it is real Python, not the Microsoft Store stub
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
    Write-Host "  Python not found. Trying to install via winget..." -ForegroundColor Yellow
    $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetCmd) {
        Write-Host "  Installing Python 3.12 (this may take 1-2 minutes)..." -ForegroundColor White
        winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
        Write-Host ""
        Write-Host "  Python installed. Please CLOSE this window and double-click setup.bat again" -ForegroundColor Cyan
        Write-Host "  so the new PATH takes effect." -ForegroundColor Cyan
        Write-Host ""
        exit 0
    } else {
        Write-Host "  Python is not installed." -ForegroundColor Red
        Write-Host "  Please install Python 3.12+ from: https://www.python.org/downloads/" -ForegroundColor White
        Write-Host "  IMPORTANT: Check 'Add Python to PATH' during install." -ForegroundColor White
        Write-Host "  Then run setup.bat again." -ForegroundColor White
        exit 1
    }
}

# --- 2. Check ffmpeg ---
Write-Host ""
Write-Host "[2/5] Checking ffmpeg..." -ForegroundColor Yellow

$ffmpegCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpegCmd) {
    Write-Host "  OK - ffmpeg is installed" -ForegroundColor Green
} else {
    Write-Host "  ffmpeg not found. Trying to install..." -ForegroundColor Yellow
    $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
    if ($wingetCmd) {
        winget install --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements --silent 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  OK - ffmpeg installed via winget" -ForegroundColor Green
        } else {
            Write-Host "  Could not install ffmpeg via winget." -ForegroundColor Yellow
            Write-Host "  Whisper transcription will not work until ffmpeg is installed." -ForegroundColor Yellow
            Write-Host "  Install later: winget install Gyan.FFmpeg" -ForegroundColor White
        }
    } else {
        Write-Host "  WARNING: ffmpeg not found. Whisper will not work." -ForegroundColor Yellow
        Write-Host "  Install from: https://ffmpeg.org/download.html" -ForegroundColor White
    }
}

# --- 3. Create virtual environment ---
Write-Host ""
Write-Host "[3/5] Setting up Python environment..." -ForegroundColor Yellow

$activateScript = Join-Path $VenvDir "Scripts\Activate.ps1"

if (Test-Path $activateScript) {
    Write-Host "  OK - Virtual environment already exists" -ForegroundColor Green
} else {
    Write-Host "  Creating virtual environment..." -ForegroundColor White
    & $pythonCmd -m venv $VenvDir
    if (-not (Test-Path $activateScript)) {
        Write-Host "  ERROR: Failed to create virtual environment." -ForegroundColor Red
        Write-Host "  Make sure Python is installed correctly." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK - Virtual environment created" -ForegroundColor Green
}

Write-Host "  Activating environment..." -ForegroundColor White
& $activateScript

Write-Host "  Upgrading pip..." -ForegroundColor White
& python -m pip install --upgrade pip --quiet 2>$null

# --- 4. Install packages ---
Write-Host ""
Write-Host "[4/5] Installing packages (1-3 minutes)..." -ForegroundColor Yellow

Write-Host "  Installing flask, flask-cors, yt-dlp, youtube-transcript-api..." -ForegroundColor White
pip install flask flask-cors yt-dlp youtube-transcript-api --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: Package installation failed." -ForegroundColor Red
    Write-Host "  Check your internet connection and try again." -ForegroundColor Red
    exit 1
}
Write-Host "  OK - Core packages installed" -ForegroundColor Green

Write-Host "  Installing Whisper AI (speech-to-text)..." -ForegroundColor White
pip install openai-whisper --quiet 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  NOTE: Whisper install failed (optional)." -ForegroundColor Yellow
    Write-Host "  Videos with existing captions will still work." -ForegroundColor Yellow
} else {
    Write-Host "  OK - Whisper installed" -ForegroundColor Green
    Write-Host "  Downloading Whisper base model (77 MB)..." -ForegroundColor White
    Write-Host "  Source: openaipublic.azureedge.net" -ForegroundColor Gray

    $modelUrl = "https://openaipublic.azureedge.net/main/whisper/models/ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e/base.pt"
    $cacheDir = Join-Path $env:USERPROFILE ".cache\whisper"
    $modelFile = Join-Path $cacheDir "base.pt"

    if (-not (Test-Path $cacheDir)) { New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null }

    # Check if already downloaded
    if ((Test-Path $modelFile) -and (Get-Item $modelFile).Length -gt 70000000) {
        Write-Host "  OK - Model already downloaded" -ForegroundColor Green
    } else {
        # Download with progress and timeout using Invoke-WebRequest
        try {
            Write-Host "  Downloading... (this may take a few minutes)" -ForegroundColor White
            $ProgressPreference = 'Continue'
            Invoke-WebRequest -Uri $modelUrl -OutFile $modelFile -TimeoutSec 120 -UseBasicParsing
            if ((Test-Path $modelFile) -and (Get-Item $modelFile).Length -gt 70000000) {
                Write-Host "  OK - Model downloaded" -ForegroundColor Green
            } else {
                Write-Host "  Download incomplete. Will retry on first use." -ForegroundColor Yellow
                Remove-Item $modelFile -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Host "  Download failed: $_" -ForegroundColor Yellow
            Write-Host "  Skipping. Will download on first use." -ForegroundColor Yellow
            Remove-Item $modelFile -Force -ErrorAction SilentlyContinue
        }
    }
}

# --- 5. Create desktop shortcut ---
Write-Host ""
Write-Host "[5/5] Creating desktop shortcut..." -ForegroundColor Yellow

try {
    $WshShell = New-Object -ComObject WScript.Shell
    $shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "B-Roll Scout.lnk"
    $shortcut = $WshShell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $CompanionDir "start-companion.bat"
    $shortcut.WorkingDirectory = $CompanionDir
    $shortcut.Description = "Start B-Roll Scout"
    $shortcut.Save()
    Write-Host "  OK - 'B-Roll Scout' shortcut created on Desktop" -ForegroundColor Green
} catch {
    Write-Host "  Could not create shortcut (non-critical): $_" -ForegroundColor Yellow
}

# --- Done ---
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Starting the companion now..." -ForegroundColor White
Write-Host "  Next time, double-click 'B-Roll Scout' on your Desktop." -ForegroundColor White
Write-Host ""

# Hand off to start-companion.ps1
$startScript = Join-Path $CompanionDir "start-companion.ps1"
if (Test-Path $startScript) {
    & $startScript
} else {
    Write-Host "  ERROR: start-companion.ps1 not found at $startScript" -ForegroundColor Red
    exit 1
}
