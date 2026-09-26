"""
execution/retention.py — optional clean-up of old run output.

OFF unless RETENTION_DAYS is set in .env (e.g. RETENTION_DAYS=45). When on, the
scheduler calls prune() once a day and deletes run output older than that:

    data/logs/report_*.json|.txt       per-test-case reports
    data/screenshots/runs/<run id>/    step screenshots
    data/plan_reports/<plan run>/      HTML + PDF plan reports
    data/plan_runs/<plan run>.json     plan run records

The newest RETENTION_KEEP_RUNS (default 20) runs of every plan are always kept,
whatever their age, together with their reports. Nothing else is touched.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time

from config.settings import DATA_DIR, LOGS_DIR

logger = logging.getLogger(__name__)
_last_day = ""


def _days() -> int:
    try:
        return max(0, int(os.getenv("RETENTION_DAYS", "0") or 0))
    except ValueError:
        return 0


def prune(now: float | None = None, dry_run: bool = False) -> dict:
    days = _days()
    if not days:
        return {"enabled": False}
    now = now or time.time()
    cutoff = now - days * 86400
    keep_runs = int(os.getenv("RETENTION_KEEP_RUNS", "20") or 20)
    runs_dir = os.path.join(DATA_DIR, "plan_runs")
    reports_dir = os.path.join(DATA_DIR, "plan_reports")
    shots_dir = os.path.join(DATA_DIR, "screenshots", "runs")

    # What the newest runs of each plan still need.
    keep_plan_runs, keep_reports, keep_run_ids = set(), set(), set()
    by_plan: dict[str, list[tuple[str, dict]]] = {}
    if os.path.isdir(runs_dir):
        for fn in os.listdir(runs_dir):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(runs_dir, fn), "r", encoding="utf-8") as f:
                        rec = json.load(f)
                except (OSError, ValueError):
                    continue
                by_plan.setdefault(rec.get("plan_id", ""), []).append((fn, rec))
    for runs in by_plan.values():
        for fn, rec in sorted(runs, key=lambda r: r[0], reverse=True)[:keep_runs]:
            keep_plan_runs.add(fn)
            keep_reports.add(fn[:-5])
            for it in rec.get("items") or []:
                if it.get("report_file"):
                    keep_reports.add(it["report_file"])
                keep_run_ids.update(it.get("run_ids") or [])

    removed = {"reports": 0, "screenshots": 0, "plan_reports": 0, "plan_runs": 0}

    def old(path: str) -> bool:
        try:
            return os.path.getmtime(path) < cutoff
        except OSError:
            return False

    def rm(path: str, key: str) -> None:
        if dry_run:
            removed[key] += 1
            return
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            removed[key] += 1
        except OSError as e:
            logger.warning("Retention: could not remove %s: %s", path, e)

    if os.path.isdir(LOGS_DIR):
        for fn in os.listdir(LOGS_DIR):
            if fn.startswith("report") and fn.endswith((".json", ".txt")):
                base = fn[:-4] + "json" if fn.endswith(".txt") else fn
                if base not in keep_reports and old(os.path.join(LOGS_DIR, fn)):
                    rm(os.path.join(LOGS_DIR, fn), "reports")
    if os.path.isdir(shots_dir):
        for d in os.listdir(shots_dir):
            if d not in keep_run_ids and old(os.path.join(shots_dir, d)):
                rm(os.path.join(shots_dir, d), "screenshots")
    if os.path.isdir(reports_dir):
        for d in os.listdir(reports_dir):
            if d not in keep_reports and old(os.path.join(reports_dir, d)):
                rm(os.path.join(reports_dir, d), "plan_reports")
    if os.path.isdir(runs_dir):
        for fn in os.listdir(runs_dir):
            p = os.path.join(runs_dir, fn)
            if fn.endswith(".json") and fn not in keep_plan_runs and old(p):
                rec_status = ""
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        rec_status = json.load(f).get("status", "")
                except (OSError, ValueError):
                    pass
                if rec_status not in ("running", "queued"):
                    rm(p, "plan_runs")
    logger.info("🧹 Retention (%s days%s): %s", days, ", dry run" if dry_run else "", removed)
    return {"enabled": True, "days": days, "dry_run": dry_run, "removed": removed}


def daily() -> None:
    """Called from the scheduler loop; runs prune() at most once per calendar day."""
    global _last_day
    today = time.strftime("%Y-%m-%d")
    if today == _last_day or not _days():
        return
    _last_day = today
    prune()
