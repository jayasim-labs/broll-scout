@echo off
:: Stops B-Roll Scout BACKGROUND processes (web app, browser helper).
:: Does NOT kill the companion (it runs in the foreground of the caller).
:: Safe to call even when nothing is running.

set "QUIET=0"
if /i "%~1"=="/quiet" set "QUIET=1"

if "%QUIET%"=="0" (
    echo.
    echo  Stopping B-Roll Scout background processes...
    echo.
)

:: Kill background Node.js web app by window title
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" >nul 2>&1

:: Kill browser-opener helper
taskkill /f /fi "WINDOWTITLE eq BRoll-OpenBrowser" >nul 2>&1

:: Also kill any orphaned Node on port 3000
:: Use a short temp filename in %TEMP% root to avoid spaces-in-path issues
set "TMP_PIDS=%TEMP%\~brpid.tmp"
netstat -ano 2>nul | findstr "LISTENING" | findstr ":3000 " > "%TMP_PIDS%" 2>nul
if exist "%TMP_PIDS%" (
    for /f "usebackq tokens=5" %%P in ("%TMP_PIDS%") do (
        taskkill /f /pid %%P >nul 2>&1
        if "%QUIET%"=="0" echo  Stopped web app PID %%P
    )
    del "%TMP_PIDS%" >nul 2>&1
)

if "%QUIET%"=="0" (
    echo.
    echo  Done.
    echo.
    pause
)
