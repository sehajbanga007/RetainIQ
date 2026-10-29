@echo off
cd /d "%~dp0"
echo Installing requirements (first time only)...
python -m pip install -r requirements.txt
python run.py
pause
