#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -e

# Get the directory where main.py and this script are located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"

# Ensure python3 is installed
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed. Please install python3." >&2
    exit 1
fi

# Check if python3-venv / python3-tk are available on strict distros like Linux Mint/Ubuntu
if ! python3 -c "import venv" &> /dev/null; then
    echo "Error: Python 'venv' module is missing."
    echo "On Linux Mint / Ubuntu, please install it via your terminal:"
    echo "  sudo apt update && sudo apt install python3-venv python3-tk -y"
    exit 1
fi

# 1. Handle Virtual Environment (Required for Linux Mint / PEP 668 externally managed environments)
if [ ! -d "$VENV_DIR" ]; then
    echo "No virtual environment found in project folder."
    echo "Creating local virtual environment (.venv)..."
    python3 -m venv "$VENV_DIR"
    echo "Virtual environment created successfully."
fi

# 2. Activate the virtual environment
source "$VENV_DIR/bin/activate"

# 3. Check and install required libraries via pip
echo "Checking required Python libraries..."
if [ -f "requirements.txt" ]; then
    pip install --upgrade pip --quiet
    pip install -r requirements.txt --quiet
else
    echo "requirements.txt not found. Installing default packages..."
    pip install ttkbootstrap Pillow sounddevice mido --quiet
fi

# 4. Run the main program
echo "Launching JoshW-Browser"
python3 main.py
