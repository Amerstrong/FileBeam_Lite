@echo off
chcp 65001 >nul
title FileBeam Lite (免费开源版) - 局域网极速互传

echo ==========================================================
echo        🚀 FileBeam Lite (免费开源版) 正在启动...
echo ==========================================================
echo.
cd /d "%~dp0"

:: 检查 Python 环境
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未检测到 Python 环境，请先安装 Python 3.8 或以上版本。
    echo 官网下载地址: https://www.python.org/
    pause
    exit /b
)

:: 后台延时 2 秒自动打开默认浏览器
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5000"

:: 启动 Flask 服务端
python server.py

pause
