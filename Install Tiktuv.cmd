@echo off
REM Double-click this to install Tiktuv.
title Installing Tiktuv
echo.
echo   Installing Tiktuv - this may take several minutes.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
  echo.
  echo   Installation did not complete. The messages above explain why.
)
echo.
pause
