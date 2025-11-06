#!/bin/bash

# SafeWatch AI Landing Page Startup Script

echo "========================================"
echo "SafeWatch AI Landing Page"
echo "========================================"
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Start the server
echo ""
echo "========================================"
echo "Starting server on http://localhost:8080"
echo "Press CTRL+C to stop"
echo "========================================"
echo ""

python server.py
