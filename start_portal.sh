#!/usr/bin/env bash
# start_portal.sh - start the Codeless Automation portal on http://localhost:8100 (macOS / Linux)
# Same as start_portal.ps1: gets the team's latest, installs new packages, keeps
# the scheduler off on laptops that do not own the schedules, then starts.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
PY="$ROOT_DIR/.venv/bin/python"
[[ -x "$PY" ]] || { echo "Run ./setup.sh first."; exit 1; }

if [[ -d .git ]]; then
  echo "Getting the latest from the team ..."
  before=$(git rev-parse HEAD 2>/dev/null)
  git pull --ff-only --quiet >/dev/null 2>&1 || true
  after=$(git rev-parse HEAD 2>/dev/null)
  if [[ "$before" != "$after" ]]; then
    echo "Updated. Making sure all packages are installed ..."
    "$PY" -m pip install -q -r requirements.txt
    "$PY" -m playwright install chromium >/dev/null 2>&1 || true
  else
    echo "Already up to date."
  fi
fi

[[ -f .env ]] || cp .env.example .env
if ! grep -qE '^\s*DISABLE_SCHEDULER\s*=' .env && ! grep -qE '^\s*SCHEDULER_OWNER\s*=\s*1' .env; then
  printf '\nDISABLE_SCHEDULER=1\n' >> .env
fi

echo "Portal starting on http://localhost:8100  (keep this window open; Ctrl+C stops it)"
exec "$PY" -m ui.app --port 8100 --show
