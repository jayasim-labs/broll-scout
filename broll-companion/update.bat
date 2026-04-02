@echo off
title B-Roll Scout - Update
color 0E

echo.
echo  B-Roll Scout - Update
echo  =====================
echo  Updates yt-dlp and all Python packages to latest versions.
echo.

set "VENV_DIR=%~dp0.venv"

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  ERROR: Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call "%VENV_DIR%\Scripts\activate.bat"

echo  Updating pip...
python -m pip install --upgrade pip --quiet

echo  Updating yt-dlp (important - YouTube changes frequently)...
pip install --upgrade yt-dlp

echo  Updating other packages...
pip install --upgrade flask flask-cors youtube-transcript-api openai-whisper --quiet

echo.
echo  Current versions:
python -c "import yt_dlp; print('  yt-dlp:', yt_dlp.version.__version__)" 2>nul
python -c "import flask; print('  Flask:', flask.__version__)" 2>nul
python -c "import whisper; print('  Whisper: OK')" 2>nul

echo.
echo  Update complete!
echo.
pause
