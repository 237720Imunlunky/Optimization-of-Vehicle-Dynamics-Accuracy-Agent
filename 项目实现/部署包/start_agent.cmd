@echo off
setlocal
title Vehicle Dynamics Agent - Start
if not exist "%~dp0start_agent.ps1" (
  echo ERROR: start_agent.ps1 was not found.
  echo Extract the ZIP completely before running this file.
  pause
  exit /b 1
)
echo Starting Agent. The browser will open after the server is ready...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_agent.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo START FAILED. See the error and logs above.
if "%EXIT_CODE%"=="0" echo Agent stopped.
pause
exit /b %EXIT_CODE%
