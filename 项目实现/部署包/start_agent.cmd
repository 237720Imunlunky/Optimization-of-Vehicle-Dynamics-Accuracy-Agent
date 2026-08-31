@echo off
chcp 65001 >nul
title 车辆动力学参数Agent - 启动
if not exist "%~dp0start_agent.ps1" (
  echo 未找到 start_agent.ps1。请先完整解压ZIP后再运行。
  pause
  exit /b 1
)
echo 正在启动Agent，启动成功后会自动打开浏览器...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_agent.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo 启动失败，请查看上方错误信息。
if "%EXIT_CODE%"=="0" echo Agent已停止。
pause
exit /b %EXIT_CODE%
