@echo off
echo Starting Whisper Swarm Book Searcher...

cd /d "%~dp0watcher"

REM Install dependencies into local .packages folder (no admin or Roaming needed)
if not exist ".packages" (
    echo Installing dependencies to .packages...
    python -m pip install -r requirements.txt --target=.packages --quiet
)

REM Use local packages so Python finds httpx, beautifulsoup4, etc.
set "PYTHONPATH=%CD%\.packages;%PYTHONPATH%"

echo Running book searcher...
python book_searcher.py

pause
