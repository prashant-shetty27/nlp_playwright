@echo off
rem Double-click to start the portal on http://127.0.0.1:8100 (close this window to stop it).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_portal.ps1"
pause
