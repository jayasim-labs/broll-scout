@echo off
title B-Roll Scout
color 0A

echo.
echo  B-Roll Scout Companion
echo  ======================
echo  Keep this window open while using B-Roll Scout.
echo  To stop: close this window or press Ctrl+C.
echo.

set "COMPANION_DIR=%~dp0"
set "VENV_DIR=%COMPANION_DIR%.venv"

:: Kill any previous instances (prevents duplicates)
call "%COMPANION_DIR%stop.bat" /quiet 2>nul

:: First-time: run setup
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  First launch detected. Running setup...
    echo.
    call "%COMPANION_DIR%setup.bat"
    exit /b 0
)

:: Activate venv
call "%VENV_DIR%\Scripts\activate.bat"

:: Quick health check
python -c "import flask" 2>nul
if %ERRORLEVEL% neq 0 (
    echo  Dependencies missing. Running setup...
    call "%COMPANION_DIR%setup.bat"
    exit /b 0
)

:: Auto-update yt-dlp (YouTube changes frequently)
echo  Checking for yt-dlp updates...
pip install --upgrade yt-dlp --quiet 2>nul
echo  OK
echo.

echo  Companion: http://127.0.0.1:9876
echo  Web app:   http://localhost:3000
echo.

:: Open browser to localhost:3000 after a short delay
start /min "" cmd /c "timeout /t 4 /nobreak >nul && start "" http://localhost:3000"

:: Run companion in foreground (blocks until Ctrl+C or window close)
python "%COMPANION_DIR%companion.py"

:: Companion exited - clean up
call "%COMPANION_DIR%stop.bat" /quiet 2>nul

echo.
echo  B-Roll Scout stopped.
echo  Press any key to close...
pause >nul
