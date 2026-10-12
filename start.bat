
@echo off
rem Launch RoadSense AI and open it in the default browser. Close this window to stop the server.
cd /d "%~dp0"
title RoadSense AI server
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start http://127.0.0.1:8000"
python run.py
pause
