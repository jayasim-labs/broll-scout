@echo off
:: Stops all B-Roll Scout processes.
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
    if %QUIET%==0 echo  Stopped companion on port 9876 (PID %%p)
)

:: Kill web app on port 3000
for /f "tokens=5" %%p in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":3000 " 2^>nul') do (
    taskkill /f /pid %%p >nul 2>&1
    if %QUIET%==0 echo  Stopped web app on port 3000 (PID %%p)
)

:: Fallback: kill by window title
taskkill /f /fi "WINDOWTITLE eq B-Roll Scout" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq BRoll-WebApp" >nul 2>&1

if %QUIET%==0 (
    echo.
    echo  All B-Roll Scout processes stopped.
    echo.
    pause
)
