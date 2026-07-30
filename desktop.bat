@echo off
chcp 65001 >nul 2>&1
title 本地服务管理 - 桌面模式

echo [信息] 正在以桌面模式启动（系统托盘）...
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

:: Install deps if needed
pip show pywebview >nul 2>&1
if errorlevel 1 (
    echo [信息] 首次运行，正在安装依赖...
    pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
)

:: Launch desktop app
cd /d "%~dp0"
pythonw -m backend.desktop

:: If pythonw fails, try python
if errorlevel 1 (
    python -m backend.desktop
)
