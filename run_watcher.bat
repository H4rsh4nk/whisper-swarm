@echo off
echo Starting Whisper Swarm Folder Watcher...

cd /d "%~dp0watcher"

if not exist venv_watcher (
    echo Creating virtual environment...
    python -m venv venv_watcher
)

echo Activating virtual environment...
call venv_watcher\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt -q

echo Starting watcher...
python watcher.py

pause
