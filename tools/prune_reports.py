"""
tools/prune_reports.py — archive run reports older than N days, then remove them.

199 reports had accumulated in data/logs/, and they only grow. But a report is
the only record of what a run actually did, so deleting one outright throws away
the answer to "when did this start failing".

So: archive first, delete second. Old reports are written into a single dated
zip under data/logs/archive/ and only removed once that zip exists and contains
them. Nothing is deleted that has not been stored somewhere else first.

    python tools/prune_reports.py            # show what would go, change nothing
    python tools/prune_reports.py --apply    # archive and remove
    python tools/prune_reports.py --days 30 --apply

Default retention is 90 days.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "data", "logs")
ARCHIVE_DIR = os.path.join(LOGS_DIR, "archive")

DEFAULT_DAYS = 90


def _older_than(days: int) -> list[str]:
    cutoff = time.time() - days * 86400
    out = []
    for name in sorted(os.listdir(LOGS_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(LOGS_DIR, name)
        if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
            out.append(path)
    return out


def prune(days: int = DEFAULT_DAYS, apply: bool = False) -> dict:
    if not os.path.isdir(LOGS_DIR):
        return {"found": 0, "archived": 0, "removed": 0, "archive": ""}

    victims = _older_than(days)
    report = {"found": len(victims), "archived": 0, "removed": 0, "archive": "",
              "cutoff": (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")}
    if not victims or not apply:
        return report

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = os.path.join(ARCHIVE_DIR, f"reports_before_{report['cutoff']}_{stamp}.zip")

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in victims:
            z.write(path, arcname=os.path.basename(path))
            report["archived"] += 1

    # Only delete what is provably inside the archive. A partial zip must not
    # cost anyone the reports it failed to store.
    with zipfile.ZipFile(archive_path) as z:
        stored = set(z.namelist())
    for path in victims:
        if os.path.basename(path) in stored:
            os.unlink(path)
            report["removed"] += 1

    report["archive"] = os.path.relpath(archive_path, BASE_DIR)
    return report


#: Screenshots are kept on a shorter leash than reports, and passing runs on a
#: much shorter one than failures. Nobody reopens the screenshots of a run that
#: passed; the failures are the whole reason the frames exist. Since most runs
#: pass, this is what keeps the footprint flat rather than growing with usage.
SHOT_DAYS_FAILED = 90
SHOT_DAYS_PASSED = 7

SCREENSHOT_RUNS_DIR = os.path.join(BASE_DIR, "data", "screenshots", "runs")


def _run_failed(run_dir_name: str) -> bool:
    """
    Did the run these screenshots belong to fail?

    Read from the run's own report. A directory whose report has been pruned
    already, or cannot be read, is treated as FAILED — keeping a folder too
    long is recoverable, deleting the evidence of a failure is not.
    """
    if not os.path.isdir(LOGS_DIR):
        return True
    for name in os.listdir(LOGS_DIR):
        if not name.endswith(".json") or run_dir_name not in name:
            continue
        try:
            with open(os.path.join(LOGS_DIR, name), "r", encoding="utf-8") as f:
                summary = (json.load(f).get("summary") or {})
            return bool(int(summary.get("failed", 0) or 0))
        except (OSError, ValueError):
            return True
    return True


def prune_screenshots(apply: bool = False) -> dict:
    """Remove run screenshot folders past their window. Never archived."""
    out = {"folders": 0, "removed": 0, "bytes": 0}
    if not os.path.isdir(SCREENSHOT_RUNS_DIR):
        return out
    now = time.time()
    for name in sorted(os.listdir(SCREENSHOT_RUNS_DIR)):
        folder = os.path.join(SCREENSHOT_RUNS_DIR, name)
        if not os.path.isdir(folder):
            continue
        out["folders"] += 1
        window = SHOT_DAYS_FAILED if _run_failed(name) else SHOT_DAYS_PASSED
        if os.path.getmtime(folder) >= now - window * 86400:
            continue
        size = sum(os.path.getsize(os.path.join(folder, f))
                   for f in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, f)))
        out["removed"] += 1
        out["bytes"] += size
        if apply:
            # Deleted outright, not archived: a zip of JPEGs saves almost
            # nothing, and these exist to be looked at soon or not at all.
            shutil.rmtree(folder, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"keep reports newer than this many days (default {DEFAULT_DAYS})")
    ap.add_argument("--apply", action="store_true",
                    help="actually archive and delete; without it nothing changes")
    args = ap.parse_args()

    r = prune(args.days, args.apply)
    shots = prune_screenshots(args.apply)
    total = len([f for f in os.listdir(LOGS_DIR) if f.endswith(".json")]) if os.path.isdir(LOGS_DIR) else 0
    print(f"reports on disk : {total}")
    print(f"older than {args.days}d : {r['found']}  (before {r.get('cutoff','')})")
    print(f"screenshot runs : {shots['folders']}  "
          f"(failed kept {SHOT_DAYS_FAILED}d, passed {SHOT_DAYS_PASSED}d)")
    print(f"  past their window : {shots['removed']}  "
          f"({shots['bytes'] / 1048576:.1f} MB)")
    if not args.apply:
        print("\nnothing changed — rerun with --apply to archive and remove them")
        return 0
    if r["found"]:
        print(f"archived        : {r['archived']} → {r['archive']}")
        print(f"removed         : {r['removed']}")
    if shots["removed"]:
        print(f"screenshots     : {shots['removed']} folder(s) deleted, "
              f"{shots['bytes'] / 1048576:.1f} MB freed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
