@echo off
setlocal
set "TARGET="
for /r "%~dp0" %%F in (install_agent.cmd) do if exist "%%~fF" set "TARGET=%%~fF"
if not defined TARGET (
  echo ERROR: install_agent.cmd was not found.
  echo Extract the ZIP completely before running this file.
  pause
  exit /b 1
)
call "%TARGET%"
