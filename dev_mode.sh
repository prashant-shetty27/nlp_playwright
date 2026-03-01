#!/bin/bash

echo "[*] Initializing Codeless Framework Development Environment..."

# 1. Activate the isolated Python environment automatically
source .venv/bin/activate

echo "[*] Environment ready. You can now execute Playwright tests in this window."
echo "[*] Snippet sync command (manual): python3 -m reporting.snippet_sync"

# 4. Replace the current script execution with an interactive shell
# This keeps your terminal open with the .venv active
exec zsh