@echo off
setlocal
title Vehicle Dynamics Agent - Install
if not exist "%~dp0install.ps1" (
  echo ERROR: install.ps1 was not found.
  echo Extract the ZIP completely before running this file.
  pause
  exit /b 1
)
echo Starting installation...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo INSTALL FAILED. See the error and logs above.
if "%EXIT_CODE%"=="0" echo INSTALL COMPLETED. Run verify_installation.cmd next.
pause
exit /b %EXIT_CODE%
