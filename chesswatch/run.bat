@echo off
cd /d "%~dp0"
python -c "import mss, chess, PIL, onnxruntime" 2>nul || python -m pip install mss pillow chess onnxruntime
python chesswatch.py
if errorlevel 1 pause
