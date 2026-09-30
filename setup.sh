#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: '$PYTHON_BIN' is not available. Install Python 3 and retry."
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Creating virtual environment at $VENV_DIR ..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo "Installing Python packages into .venv ..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

echo "Installing Playwright browsers ..."
"$VENV_DIR/bin/python" -m playwright install chromium

# Same first-run files as setup.ps1 (Windows), so every OS starts identically.
if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo ".env created from .env.example - fill in the values you were given."
fi
for d in data/logs data/screenshots/runs data/plan_runs data/plan_reports data/testsigma_exports; do
  mkdir -p "$ROOT_DIR/$d"
done
if [[ ! -f "$ROOT_DIR/config/environments.json" && -f "$ROOT_DIR/config/environments.example.json" ]]; then
  cp "$ROOT_DIR/config/environments.example.json" "$ROOT_DIR/config/environments.json"
fi
if [[ ! -f "$ROOT_DIR/data/common/variables.json" && -f "$ROOT_DIR/data/common/variables.example.json" ]]; then
  cp "$ROOT_DIR/data/common/variables.example.json" "$ROOT_DIR/data/common/variables.json"
fi

"$VENV_DIR/bin/python" "$ROOT_DIR/tools/check_commit.py" --install || true
echo "Setup complete."
echo "Use: . .venv/bin/activate"
echo "Start the portal: .venv/bin/python -m ui.app --port 8100 --show"
