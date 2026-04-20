#!/usr/bin/env bash
# start_all.sh — Launch the NLP Playwright Control Panel
#
# The control panel starts on http://localhost:8000 and automatically
# boots all servers: Web Recorder (8080), Android Recorder (8090),
# iOS Recorder (8091), Appium (4723), and Spy Server (5050).
#
# Usage:
#   ./start_all.sh                 # port 8000
#   ./start_all.sh --port 9000
#   ./start_all.sh --no-auto-start  # open panel without starting servers

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "❌  Python venv not found: $PY"
  echo "    Run the project setup first to create .venv"
  exit 1
fi

mkdir -p "$ROOT_DIR/data/logs"

echo "╔══════════════════════════════════════════════════════╗"
echo "║     NLP Playwright — Control Panel Launcher          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Launching control panel on http://localhost:8000"
echo "  All servers will auto-start in the background."
echo "  Use the 'Start All Servers' button to restart any."
echo ""

exec "$PY" "$ROOT_DIR/control_panel.py" "$@"
