#!/usr/bin/env bash
# One-time server setup for the QA Codeless Automation portal (Ubuntu 22.04 / Debian 12).
# Run as root (sudo) from anywhere:  sudo bash deploy/server_setup.sh <git-clone-url>
#
# What it does:
#   1. creates a service user `qaportal` and /opt/qa-portal
#   2. installs Python 3.11, git and the OS libraries Playwright's browsers need
#   3. clones the repository (or updates it), creates .venv, installs Python packages
#   4. downloads Chromium + WebKit for Playwright (headless runs on the server)
#   5. creates .env from deploy/.env.server.example if there is none — FILL IT IN afterwards
#   6. installs and starts the systemd service on port 8100
set -euo pipefail
REPO_URL="${1:-}"
APP_DIR=/opt/qa-portal
SVC_USER=qaportal

if [[ -z "$REPO_URL" && ! -d "$APP_DIR/.git" ]]; then
  echo "usage: sudo bash deploy/server_setup.sh <git-clone-url>"; exit 1
fi

echo "== 1. service user and folder"
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$SVC_USER"
mkdir -p "$APP_DIR"

echo "== 2. OS packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git curl ca-certificates software-properties-common
if ! command -v python3.11 >/dev/null 2>&1; then
  add-apt-repository -y ppa:deadsnakes/ppa 2>/dev/null || true
  apt-get update -q
  apt-get install -y -q python3.11 python3.11-venv python3.11-dev
fi

echo "== 3. code"
if [[ -d "$APP_DIR/.git" ]]; then
  sudo -u "$SVC_USER" git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO_URL" "$APP_DIR"
fi
chown -R "$SVC_USER:$SVC_USER" "$APP_DIR"
sudo -u "$SVC_USER" bash -c "
  cd $APP_DIR
  [[ -d .venv ]] || python3.11 -m venv .venv
  .venv/bin/pip install --upgrade pip -q
  .venv/bin/pip install -q -r requirements.txt
"

echo "== 4. browsers (Chromium + WebKit) and their OS libraries"
export PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.pw-browsers"
"$APP_DIR/.venv/bin/python" -m playwright install-deps chromium webkit
sudo -u "$SVC_USER" PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_BROWSERS_PATH" \
  "$APP_DIR/.venv/bin/python" -m playwright install chromium webkit

echo "== 5. runtime files"
sudo -u "$SVC_USER" bash -c "
  cd $APP_DIR
  [[ -f .env ]] || cp deploy/.env.server.example .env
  for d in data/logs data/screenshots/runs data/plan_runs data/plan_reports data/testsigma_exports data/videos/completed; do mkdir -p \$d; done
  [[ -f config/environments.json ]] || cp config/environments.example.json config/environments.json
  [[ -f data/common/variables.json ]] || cp data/common/variables.example.json data/common/variables.json
"
chmod 600 "$APP_DIR/.env"

echo "== 6. service"
cp "$APP_DIR/deploy/qa-portal.service" /etc/systemd/system/qa-portal.service
systemctl daemon-reload
systemctl enable --now qa-portal
sleep 5
systemctl --no-pager --lines=5 status qa-portal || true
echo
echo "Done. Portal: http://$(hostname -I | awk '{print $1}'):8100"
echo "Next: edit $APP_DIR/.env (credentials — see deploy/DEPLOYMENT.md), then: sudo systemctl restart qa-portal"
echo "Nightly backup: sudo cp deploy/backup.sh /etc/cron.daily/qa-portal-backup && sudo chmod +x /etc/cron.daily/qa-portal-backup"
