@echo off
title B-Roll Scout Companion
color 0A

echo.
echo  B-Roll Scout Companion
echo  ----------------------
echo  Keep this window open while using B-Roll Scout in your browser.
echo.

set COMPANION_DIR=%~dp0
set VENV_DIR=%COMPANION_DIR%.venv

:: -----------------------------------------------------------
:: Kill any previous instances first (prevents duplicates)
:: -----------------------------------------------------------
call "%COMPANION_DIR%stop.bat" /quiet 2>nul

:: Auto-run setup if not yet installed
if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  First launch detected. Running setup...
    echo.
    call "%COMPANION_DIR%setup.bat" /nolaunch
    if %ERRORLEVEL% neq 0 (
        echo  Setup failed. Please try running setup.bat manually.
        pause
        exit /b 1
    )
    echo.
    echo  Setup complete! Starting companion now...
    echo.
)

:: Activate venv
call "%VENV_DIR%\Scripts\activate.bat"

:: Quick health check
python -c "import flask" 2>nul
if %ERRORLEVEL% neq 0 (
    echo  Dependencies missing. Re-running setup...
    call "%COMPANION_DIR%setup.bat" /nolaunch
    call "%VENV_DIR%\Scripts\activate.bat"
)

:: Auto-update yt-dlp silently (YouTube changes frequently)
echo  Checking for yt-dlp updates...
pip install --upgrade yt-dlp --quiet 2>nul
echo  OK
echo.

echo  Starting on http://127.0.0.1:9876 ...
echo.
echo  ============================================================
echo   To STOP: close this window, or press Ctrl+C
echo  ============================================================
echo.

:: Open the web app if app.url is configured
call "%COMPANION_DIR%load-app-url.bat"
if not "%BROLL_WEB_URL%"=="" (
    start /min "" cmd /c "timeout /t 4 /nobreak >nul && start "" %BROLL_WEB_URL%"
) else (
    echo  Tip: Copy app.url.example to app.url with your web app URL.
    echo.
)

:: Run companion in foreground -- blocks until Ctrl+C or window close
python "%COMPANION_DIR%companion.py"

:: Companion exited -- clean up any leftover processes
call "%COMPANION_DIR%stop.bat" /quiet 2>nul

echo.
echo  Companion stopped.
pause
