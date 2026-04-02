@echo off
rem Sets BROLL_WEB_URL from the first non-empty line in app.url (same folder as this script).
rem Lines starting with # are ignored. Copy app.url.example to app.url and edit.
set "BROLL_WEB_URL="
if not exist "%~dp0app.url" goto :eof
for /f "usebackq eol=# delims=" %%a in ("%~dp0app.url") do (
  set "BROLL_WEB_URL=%%a"
  goto :eof
)
