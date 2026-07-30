@echo off
chcp 65001 >nul 2>&1
title 打包 - 本地服务管理

echo ============================================
echo   本地服务管理 - 打包为 EXE
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python
    pause
    exit /b 1
)

:: Install build dependencies
echo [1/3] 安装打包依赖...
pip install pyinstaller -q
pip install -r "%~dp0requirements.txt" -q
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

:: Clean previous build
echo [2/3] 清理旧构建...
if exist "%~dp0build" rd /s /q "%~dp0build"
if exist "%~dp0dist" rd /s /q "%~dp0dist"

:: Build
echo [3/3] 正在打包（可能需要几分钟）...
cd /d "%~dp0"
pyinstaller build.spec --noconfirm
if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请检查上方错误信息
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包成功！
echo   输出目录: dist\本地服务管理\
echo   可执行文件: dist\本地服务管理\本地服务管理.exe
echo ============================================
echo.
echo 提示: 将整个 dist\本地服务管理 文件夹复制到任意位置即可使用
echo.
pause
