@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "fastapi_app\main.py"
pause
