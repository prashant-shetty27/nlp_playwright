# QA Codeless Automation portal — hosting handover for the Linux team

Prepared 30 Sep 2026 for the Justdial Linux/infra team. Owner: Prashant Shetty (QA). Questions → Prashant on Slack.

## 1. What this is
A web portal (Python, FastAPI + NiceGUI) where QA writes and runs codeless UI tests (Playwright) for justdial.com — website, mobile site (device emulation), and later Android/iOS via Appium. One central instance replaces the current per-laptop installs. Testers use it in a browser; nothing is installed on their machines.

## 2. Repository
- Source: currently GitHub `prashant-shetty27/nlp_playwright` (private). To be moved to a NEW internal GitLab project **codeless-automation** (Prashant has a GitLab account; `qa-portal` is a different project). Suggested: create the GitLab project, then Prashant pushes `main` there once (`git remote add gitlab <url> && git push gitlab main`); GitHub can be retired after.
- The repo contains **no secrets**: `.env`, credentials, user accounts, Test Data with logins, screenshots, reports, run state and Testsigma exports are git-ignored (see `.gitignore`). Everything the server needs at runtime that is not in git is created by `deploy/server_setup.sh` from the `*.example` templates and then filled in by Prashant.

## 3. Server requirements
| Item | Value |
|---|---|
| OS | Ubuntu 22.04 LTS or Debian 12 (x86_64); RHEL-family also works but `server_setup.sh` uses apt |
| CPU / RAM / disk | 4 vCPU, **8 GB RAM** (portal ≈ 0.5 GB; each headless browser run ≈ 0.5–1 GB; 3–4 parallel runs), **30 GB disk** (20 GB minimum): app + Python packages + browsers ≈ 3 GB; screenshots ≈ 2.5 MB per run, kept **14 days** (≈ 5 GB at ~150 runs/day); reports, logs, backups ≈ 1–2 GB |
| Python | 3.11 (script installs it via deadsnakes if missing) |
| Network | On the office network (no VPN needed on the server). Users reach it from the offices or over VPN. Internet access needed at install time only (pip packages, Playwright browser download) |
| Inbound | TCP **8100** from office/VPN ranges (or 443 via nginx reverse proxy with an internal cert — optional) |
| Whitelisting | The server's **IP must be added to the automation whitelist** the same way Testsigma's IPs were (justdial staging/prod bot protection, OTP portal) |
| DNS | Please create `codeless-automation.justdial.internal` (or similar) → server IP |
| Service user | `codeless` (created by the script), app in `/opt/codeless-automation` |

## 4. Install (≈10 minutes)
```
sudo bash deploy/server_setup.sh <git-clone-url>
sudo nano /opt/codeless-automation/.env        # fill in the <fill> values — Prashant supplies them (see §6)
sudo systemctl restart codeless-automation
sudo cp /opt/codeless-automation/deploy/backup.sh /etc/cron.daily/codeless-automation-backup && sudo chmod +x /etc/cron.daily/codeless-automation-backup
```
Check: `curl -s http://127.0.0.1:8100/health` → 200; open `http://<server>:8100` → login page; the first visit creates the admin account.

Updates later: `sudo bash /opt/codeless-automation/deploy/update.sh` (git pull, packages, browsers, restart). The portal also has Settings → restart for admins; a "Update portal" button that calls the same steps is planned.

## 5. What runs and where
- `codeless-automation.service` (systemd): `python -m ui.app --host 0.0.0.0 --port 8100`. Logs: `journalctl -u codeless-automation` and `/opt/codeless-automation/data/logs/portal.log` (rotating).
- Headless Chromium/WebKit are launched by the service for test runs (no display needed). Playwright browsers live in `/opt/codeless-automation/.pw-browsers`.
- Scheduler runs inside the service (`SCHEDULER_OWNER=1` on the server only).
- Future: "agent" processes on office PCs for real phones / visible-browser runs connect **to** the server over HTTP (8100); nothing inbound to the PCs.

## 6. Configuration (`/opt/codeless-automation/.env`) — names only; values come from Prashant, never over chat/email in clear
- Portal: `UI_HOST`, `UI_PORT`, `UI_SESSION_SECRET` (random), `PORTAL_BASE_URL`, `RETENTION_DAYS`, `SCHEDULER_OWNER=1`
- Staging HTTP Basic logins: `AUTH_<NAME>_DOMAIN / _USERNAME / _PASSWORD` for prot3, prot, prot4, devx, seo, designtest, staging2
- OTP test portal: `OTP_PORTAL_URL`, `OTP_PORTAL_USERNAME`, `OTP_PORTAL_PIN`; test mobile numbers `JD_TEST_MOBILE*`
- Notifications: `NOTIFY_ON_SLACK`, `SLACK_WEBHOOK_URL` or `SLACK_BOT_TOKEN` + `SLACK_REPORT_CHANNEL`; optional e-mail `EMAIL_SMTP_*`
- Integrations (optional): `JIRA_BASE_URL` + `JIRA_PAT`, `TESTSIGMA_BASE_URL` + `TESTSIGMA_API_KEY`, `ANTHROPIC_API_KEY` / `LLM_PROVIDER`
Full template with comments: `deploy/.env.server.example`.

## 7. Data on the server (back up these)
`/opt/codeless-automation/`: `flows/` (test cases), `suites/`, `plans/`, `data/locators_manual.json` (elements), `data/reusable_steps.json`, `data/test_case_folders.json`, `data/users.json`, `data/common/` (Test Data), `config/environments.json`, `data/plan_runs/`, `data/plan_reports/`, `data/logs/`, `.env`. `deploy/backup.sh` (cron.daily) tars these to `/var/backups/codeless-automation`, 14 days kept (a few MB each). Screenshots (`data/screenshots/`) and videos are excluded from backup by design. Screenshots and per-run reports older than 14 days are deleted automatically (`RETENTION_DAYS=14`; the newest 20 runs of every plan are always kept).

## 8. Security notes
- `.env` is `chmod 600 codeless`. No secrets in git. Portal logins are local accounts with roles (admin/editor/viewer); passwords hashed. Session cookie signed with `UI_SESSION_SECRET`.
- The portal is intended for the internal network only — do not expose 8100 to the internet.
- Test runs drive justdial staging/prod pages as a normal browser; lead-sending steps are switched off in test cases by default.

## 9. Support / rollback
- Restart: `sudo systemctl restart codeless-automation`. Rollback: `cd /opt/codeless-automation && sudo -u codeless git checkout <previous-commit> && sudo systemctl restart codeless-automation`.
- Contact: Prashant Shetty (QA). Code changes are made by Prashant (with Claude) and pushed to `main`; the server is updated with `deploy/update.sh`.
