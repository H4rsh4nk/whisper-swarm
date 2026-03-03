@echo off
echo Starting Dreame FM Scraper (auto: discover, download, upload to master)...

cd /d "%~dp0watcher"

REM Install dependencies into local .packages folder if needed
if not exist ".packages" (
    echo Installing dependencies to .packages...
    python -m pip install -r requirements.txt --target=.packages --quiet
    echo Installing Playwright Chromium...
    python -m playwright install chromium
)

set "PYTHONPATH=%CD%\.packages;%PYTHONPATH%"

REM Run with no args = hardcoded URL + upload to master
python dreame_scraper.py

pause
