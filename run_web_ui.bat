@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "web_ui\app.py" --host 0.0.0.0 --port 5000
pause
