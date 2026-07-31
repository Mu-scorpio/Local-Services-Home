@echo off
chcp 65001 >nul 2>&1
title 打包 - 本地服务管理

echo ============================================
echo   本地服务管理 - 打包为单个 EXE
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python
    pause
    exit /b 1
)

echo [1/3] 安装打包依赖...
pip install pyinstaller -q
pip install -r "%~dp0requirements.txt" -q
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo [2/3] 清理旧构建...
if exist "%~dp0build" rd /s /q "%~dp0build"
if exist "%~dp0dist" rd /s /q "%~dp0dist"

echo [3/3] 正在打包（onefile，可能需要几分钟）...
cd /d "%~dp0"
pyinstaller build.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请检查上方错误信息
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包成功！
echo   可执行文件: dist\本地服务管理.exe
echo ============================================
echo.
echo 提示: 只需复制这一个 exe 即可使用（用户数据在 %%LOCALAPPDATA%%\Local Services Home）
echo.
pause
