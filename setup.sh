#!/bin/bash

echo "Installing Python packages..."
pip3 install -r requirements.txt

echo "Installing Playwright browsers..."
playwright install

echo "Setup complete! You can now run ./run.sh"
