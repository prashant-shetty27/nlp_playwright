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
import os
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"keep reports newer than this many days (default {DEFAULT_DAYS})")
    ap.add_argument("--apply", action="store_true",
                    help="actually archive and delete; without it nothing changes")
    args = ap.parse_args()

    r = prune(args.days, args.apply)
    total = len([f for f in os.listdir(LOGS_DIR) if f.endswith(".json")]) if os.path.isdir(LOGS_DIR) else 0
    print(f"reports on disk : {total}")
    print(f"older than {args.days}d : {r['found']}  (before {r.get('cutoff','')})")
    if not args.apply:
        print("\nnothing changed — rerun with --apply to archive and remove them")
        return 0
    if r["found"]:
        print(f"archived        : {r['archived']} → {r['archive']}")
        print(f"removed         : {r['removed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
