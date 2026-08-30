@echo off
chcp 65001 >nul
title 车辆动力学参数Agent - 部署验收
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify_installation.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo 验收失败，请查看上方缺失项。
if "%EXIT_CODE%"=="0" echo 验收完成。
pause
exit /b %EXIT_CODE%
