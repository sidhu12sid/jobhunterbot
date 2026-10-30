@echo off
:: Move to project root directory
cd /d "%~dp0"

:: Run discovery and application engine using virtual environment Python
call .venv\Scripts\python.exe main.py --max-apps 10 >> run_daily.log 2>&1
