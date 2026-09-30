#!/usr/bin/env bash
# Update the portal to the latest main and restart (run by the Linux team or from a CI job).
set -euo pipefail
APP_DIR=/opt/qa-portal
sudo -u qaportal git -C "$APP_DIR" pull --ff-only
sudo -u qaportal "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
sudo -u qaportal PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.pw-browsers" "$APP_DIR/.venv/bin/python" -m playwright install chromium webkit >/dev/null
systemctl restart qa-portal
systemctl --no-pager --lines=3 status qa-portal
