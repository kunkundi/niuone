@echo off
setlocal

cd /d "%~dp0"

where powershell >nul 2>nul
if errorlevel 1 (
  echo PowerShell is required to run NiuOne on Windows.
  echo Please install PowerShell or start the dashboard manually with Python.
  pause
  exit /b 1
)

powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set EXIT_CODE=%ERRORLEVEL%

if not "%EXIT_CODE%"=="0" (
  echo.
  echo NiuOne stopped with exit code %EXIT_CODE%.
  pause
)

exit /b %EXIT_CODE%
