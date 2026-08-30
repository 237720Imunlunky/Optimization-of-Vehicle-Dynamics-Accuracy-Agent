@echo off
chcp 65001 >nul
title 车辆动力学参数Agent - 启动
echo 正在启动Agent，启动成功后会自动打开浏览器...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_agent.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo 启动失败，请查看上方错误信息。
if "%EXIT_CODE%"=="0" echo Agent已停止。
pause
exit /b %EXIT_CODE%
