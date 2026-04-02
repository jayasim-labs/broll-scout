@echo off
REM B-Roll Scout - Daily launcher for editors
REM Double-click this file (or the Desktop shortcut) to start B-Roll Scout.
title B-Roll Scout
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-companion.ps1"
if errorlevel 1 (
  echo.
  echo Companion exited with an error. See messages above.
)
echo.
echo Press any key to close...
pause >nul
