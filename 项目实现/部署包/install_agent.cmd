@echo off
chcp 65001 >nul
title 车辆动力学参数Agent - 安装
echo 正在从部署包所在目录启动安装脚本...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo 安装失败，请查看上方错误信息。
  pause
  exit /b 1
)
echo.
echo 安装完成。下一步请双击 start_agent.cmd。
pause
