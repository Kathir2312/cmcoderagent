@echo off
rem Installs cmcoder for this user (no administrator rights). Double-click it.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (echo. & echo Installation failed: see the message above.)
pause
