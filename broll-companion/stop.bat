@echo off
:: Stops all B-Roll Scout processes (companion + web app).
:: Double-click to force-stop, or called automatically by start-companion.bat.

set QUIET=0
if /i "%~1"=="/quiet" set QUIET=1

if %QUIET%==0 (
    echo.
    echo  Stopping B-Roll Scout...
    echo.
)

:: Kill companion Flask on port 9876
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":9876 " 2^>nul') do (
    taskkill /f /pid %%p >nul 2>&1
    if %QUIET%==0 echo  Stopped companion (PID %%p)
)

:: Kill anything on port 3000 (web app if running locally)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":3000 " 2^>nul') do (
    taskkill /f /pid %%p >nul 2>&1
    if %QUIET%==0 echo  Stopped web app (PID %%p)
)

:: Fallback: kill by window title
taskkill /f /fi "WINDOWTITLE eq B-Roll Scout Companion" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" >nul 2>&1

if %QUIET%==0 (
    echo.
    echo  All B-Roll Scout processes stopped.
    pause
)
