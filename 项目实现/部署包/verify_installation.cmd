@echo off
chcp 65001 >nul
title 车辆动力学参数Agent - 部署验收
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify_installation.ps1"
if errorlevel 1 (
  echo.
  echo 验收失败，请查看上方缺失项。
  pause
  exit /b 1
)
echo.
echo 验收完成。
pause
