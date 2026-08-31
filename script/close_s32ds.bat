@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "close_s32ds.ps1"
pause
