@echo off
chcp 65001 >nul
title 车辆动力学参数Agent - 启动
echo 正在启动Agent，本窗口关闭后Agent也会停止...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_agent.ps1"
if errorlevel 1 (
  echo.
  echo 启动失败，请查看上方错误信息。
  pause
  exit /b 1
)
