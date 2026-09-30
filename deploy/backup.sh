#!/usr/bin/env bash
# Nightly backup of everything the portal cannot recreate: test cases, elements,
# step groups, suites, plans, users, test data, config, reports. Screenshots and
# videos are excluded (large, recreated by re-running). Keeps 30 days.
set -euo pipefail
APP_DIR=/opt/qa-portal
DEST=${BACKUP_DIR:-/var/backups/qa-portal}
mkdir -p "$DEST"
STAMP=$(date +%Y%m%d_%H%M)
tar -czf "$DEST/qa-portal_$STAMP.tgz" -C "$APP_DIR" \
  flows suites plans data/locators_manual.json data/reusable_steps.json data/test_case_folders.json \
  data/users.json data/common data/plan_runs data/plan_reports data/logs config .env 2>/dev/null
find "$DEST" -name 'qa-portal_*.tgz' -mtime +30 -delete
