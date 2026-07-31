@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Build Local Services Home

echo ============================================
echo   Local Services Home - Build single EXE
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

echo [1/3] Installing build dependencies...
python -m pip install -q pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install pyinstaller.
    pause
    exit /b 1
)
python -m pip install -q -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] Failed to install requirements.txt
    pause
    exit /b 1
)

echo [2/3] Cleaning old build output...
if exist "%~dp0build" rd /s /q "%~dp0build"
if exist "%~dp0dist" rd /s /q "%~dp0dist"

echo [3/3] Packaging with PyInstaller (onefile, may take a few minutes)...
python -m PyInstaller "%~dp0build.spec" --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See messages above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build OK
echo   Output: dist\ (single .exe inside)
echo ============================================
echo.
echo Copy that one .exe anywhere to use it.
echo User data: %%LOCALAPPDATA%%\Local Services Home
echo.
pause
endlocal
