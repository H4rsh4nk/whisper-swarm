#!/bin/bash
echo "Starting Whisper Swarm Folder Watcher..."

cd "$(dirname "$0")/watcher"

if [ ! -d "venv_watcher" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv_watcher
fi

echo "Activating virtual environment..."
source venv_watcher/bin/activate

echo "Installing dependencies..."
pip install -r requirements.txt -q

echo "Starting watcher..."
python watcher.py
