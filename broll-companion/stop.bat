@echo off
:: Stops B-Roll Scout background processes.
:: Safe to call even if nothing is running.

set QUIET=0
if /i "%~1"=="/quiet" set QUIET=1

if %QUIET%==0 (
    echo.
    echo  Stopping B-Roll Scout...
    echo.
)

:: Write port scan results to a temp file to avoid for/f pipe issues
set "TMPFILE=%TEMP%\broll_stop_pids.txt"

:: Find PIDs on port 9876 (companion)
netstat -ano 2>nul | findstr "LISTENING" | findstr ":9876 " > "%TMPFILE%" 2>nul
if exist "%TMPFILE%" (
    for /f "tokens=5" %%p in (%TMPFILE%) do (
        if not "%%p"=="" (
            taskkill /f /pid %%p >nul 2>&1
            if %QUIET%==0 echo  Stopped companion (PID %%p)
        )
    )
)

:: Find PIDs on port 3000 (web app)
netstat -ano 2>nul | findstr "LISTENING" | findstr ":3000 " > "%TMPFILE%" 2>nul
if exist "%TMPFILE%" (
    for /f "tokens=5" %%p in (%TMPFILE%) do (
        if not "%%p"=="" (
            taskkill /f /pid %%p >nul 2>&1
            if %QUIET%==0 echo  Stopped web app (PID %%p)
        )
    )
)

del "%TMPFILE%" >nul 2>&1

:: Fallback: kill by window title (only exact match targets)
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" >nul 2>&1

if %QUIET%==0 (
    echo.
    echo  Done.
    echo.
    pause
)
