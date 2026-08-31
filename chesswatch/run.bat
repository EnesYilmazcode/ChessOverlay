@echo off
cd /d "%~dp0"
python -c "import mss, chess, PIL" 2>nul || python -m pip install mss pillow chess
python chesswatch.py
if errorlevel 1 pause
