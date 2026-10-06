@echo off
rem Removes cmcoder for this user (your settings and keys are kept).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
pause
