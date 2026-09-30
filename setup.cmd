@echo off
rem Double-click to run the one-time setup (bypasses the PowerShell script block for this run only).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
