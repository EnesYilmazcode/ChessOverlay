@echo off
cd /d "%~dp0"
rem The recorder needs these three and cannot start without them.
python -c "import mss, chess, PIL" 2>nul || python -m pip install mss pillow chess
rem onnxruntime is only for the Rating slider, which greys out without it, so it
rem is installed on its own and a machine with no wheel for it still gets the app.
python -c "import onnxruntime" 2>nul || python -m pip install onnxruntime || echo onnxruntime would not install, so the Rating slider will be off.
python chesswatch.py
if errorlevel 1 pause
