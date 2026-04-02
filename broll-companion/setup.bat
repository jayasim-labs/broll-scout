@echo off
REM B-Roll Scout - One-click setup for editors
REM Double-click this file to install everything needed.
title B-Roll Scout - Setup
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup failed. See messages above.
)
echo.
echo Press any key to close...
pause >nul
