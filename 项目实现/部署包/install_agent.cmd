@echo off
chcp 65001 >nul
title 车辆动力学参数Agent - 安装
if not exist "%~dp0install.ps1" (
  echo 未找到 install.ps1。
  echo 请不要直接在ZIP压缩包内部运行，必须先右键ZIP选择“全部解压”，再进入解压后的部署包目录。
  pause
  exit /b 1
)
echo 正在从部署包所在目录启动安装脚本...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo 安装失败，请查看上方错误信息。
if "%EXIT_CODE%"=="0" echo 安装完成。下一步请双击 start_agent.cmd。
pause
exit /b %EXIT_CODE%
