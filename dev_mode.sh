#!/bin/bash

echo "[*] Initializing Codeless Framework Development Environment..."

# 1. Activate the isolated Python environment automatically
source .venv/bin/activate

# 2. Check if the daemon is already running to prevent duplicates
if pgrep -f "python3 auto_sync_daemon.py" > /dev/null
then
    echo "[!] Auto-Sync Daemon is already running."
else
    # 3. Start the daemon in the background using the '&' operator
    python3 auto_sync_daemon.py &
    DAEMON_PID=$!
    echo "[+] Auto-Sync Daemon engaged in background (PID: $DAEMON_PID)"
    echo "[*] To stop the daemon later, type: kill $DAEMON_PID"
fi

echo "[*] Environment ready. You can now execute Playwright tests in this window."

# 4. Replace the current script execution with an interactive shell
# This keeps your terminal open with the .venv active
exec zsh