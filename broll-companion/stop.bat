@echo off
REM B-Roll Scout - Stop all background processes
REM Double-click to force-stop, or called automatically.
title B-Roll Scout - Stop
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "taskkill /f /fi 'WINDOWTITLE eq BRoll-WebApp' 2>$null; " ^
  "taskkill /f /fi 'WINDOWTITLE eq BRoll-OpenBrowser' 2>$null; " ^
  "$p = netstat -ano 2>$null | Select-String 'LISTENING' | Select-String ':3000 '; " ^
  "if ($p) { foreach ($l in $p) { $id = ($l -split '\s+')[-1]; if ($id -match '^\d+$') { taskkill /f /pid $id 2>$null } } }; " ^
  "$p = netstat -ano 2>$null | Select-String 'LISTENING' | Select-String ':9876 '; " ^
  "if ($p) { foreach ($l in $p) { $id = ($l -split '\s+')[-1]; if ($id -match '^\d+$') { taskkill /f /pid $id 2>$null } } }; " ^
  "Write-Host ''; Write-Host '  All B-Roll Scout processes stopped.' -ForegroundColor Green"

echo.
echo Press any key to close...
pause >nul
