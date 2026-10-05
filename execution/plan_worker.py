"""
execution/plan_worker.py — run ONE test case of a plan run in its own process.

    python -m execution.plan_worker <plan_run_id> <item_index> <out.json>

Started by plan_engine._run_parallel. Runs exactly what the sequential plan
loop runs (plan_engine._run_item: prepare, run, retry rules, attempt history)
and writes the item's fields to <out.json> after every change, so the portal
can show progress and merge the result. Separate processes keep each run's
process-wide state (HEADLESS, runtime variables, last typed mobile, caches)
apart.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    run_id, idx, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, base)
    os.chdir(base)
    from execution import plan_engine as P

    rec = P.get_run(run_id)
    item = rec["items"][idx]
    item["status"] = "pending"
    ex = rec.get("execution") or {}

    def _write(_rec=None) -> None:
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(item, f, ensure_ascii=False)
        os.replace(tmp, out)

    P._save = _write            # never write the shared plan record from a worker
    try:
        P._run_item(rec, item, ex)
    except Exception as e:  # noqa: BLE001
        item.update(status="not_run", reason=f"worker error: {str(e)[:300]}")
    _write()
    return 0


if __name__ == "__main__":
    sys.exit(main())
