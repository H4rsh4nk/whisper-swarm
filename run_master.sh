#!/bin/bash
echo "============================================"
echo "  Distributed STT - Master Server"
echo "============================================"
echo

cd "$(dirname "$0")"

# Check if venv exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r master/requirements.txt -q

# Get IP address
echo
echo "Your IP addresses:"
if [[ "$OSTYPE" == "darwin"* ]]; then
    ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}'
else
    hostname -I
fi
echo
echo "Share one of these IPs with your workers!"
echo "Dashboard will be at: http://localhost:8000"
echo

# Run server
python master/server.py
