#!/usr/bin/env bash
# Update the portal to the latest main and restart (run by the Linux team or from a CI job).
set -euo pipefail
APP_DIR=/opt/codeless-automation
sudo -u codeless git -C "$APP_DIR" pull --ff-only
sudo -u codeless "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
sudo -u codeless PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.pw-browsers" "$APP_DIR/.venv/bin/python" -m playwright install chromium webkit >/dev/null
systemctl restart codeless-automation
systemctl --no-pager --lines=3 status codeless-automation
