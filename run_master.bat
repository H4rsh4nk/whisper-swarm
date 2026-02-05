@echo off
echo ============================================
echo   Distributed STT - Master Server
echo ============================================
echo.

cd /d "%~dp0"

REM Check if venv exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate venv
call venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install -r master\requirements.txt -q

REM Get IP address
echo.
echo Your IP addresses:
ipconfig | findstr /i "IPv4"
echo.
echo Share one of these IPs with your workers!
echo Dashboard will be at: http://localhost:8000
echo.

REM Run server
python master\server.py

pause
