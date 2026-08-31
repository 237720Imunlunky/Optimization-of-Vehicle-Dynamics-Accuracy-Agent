@echo off
setlocal
title Vehicle Dynamics Agent - Verify
if not exist "%~dp0verify_installation.ps1" (
  echo ERROR: verify_installation.ps1 was not found.
  echo Extract the ZIP completely before running this file.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify_installation.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" echo VERIFICATION FAILED. See the error and logs above.
if "%EXIT_CODE%"=="0" echo VERIFICATION COMPLETED.
pause
exit /b %EXIT_CODE%
