@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
  pause
  exit /b 1
)

python -m backend.launcher start
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  pause
)
exit /b %ERR%