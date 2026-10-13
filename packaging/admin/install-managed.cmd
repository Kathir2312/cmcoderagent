@echo off
rem Installs managed-settings.json (next to this file) for every user of this PC.
rem Right-click it, Run as administrator.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-managed.ps1" %*
if errorlevel 1 (echo. & echo Not installed: see the message above.)
pause
