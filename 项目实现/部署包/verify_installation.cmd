@echo off
chcp 65001 >nul
title 车辆动力学参数Agent - 部署验收
if not exist "%~dp0verify_installation.ps1" (
  echo 未找到 verify_installation.ps1。请先完整解压ZIP后再运行。
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify_installation.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo 验收失败，请查看上方缺失项。
if "%EXIT_CODE%"=="0" echo 验收完成。
pause
exit /b %EXIT_CODE%
