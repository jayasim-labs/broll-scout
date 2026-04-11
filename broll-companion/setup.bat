@echo off
REM B-Roll Scout - Smart setup + launcher for editors
REM Double-click this file to install (first time) or launch (daily use).
REM Skips what's already installed automatically.
title B-Roll Scout
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo B-Roll Scout exited. See messages above.
)
echo.
echo Press any key to close...
pause >nul
