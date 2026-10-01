"""
tools/apply_ignore_from_testsigma.py — mark already-imported steps "[ignore]".

Testsigma's "Ignore step result" was not carried over by older imports. This
re-converts each saved Testsigma export in memory, takes the lines the importer
now marks [ignore], and adds the marker to the SAME line in the test case /
step group we already have — nothing else in the file changes (edits made since
the import are kept). Dry run by default; --apply writes.
"""
from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.step_flags import join, split  # noqa: E402
from tools import testsigma_pull as tp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mark(existing: list[str], wanted: list[str]) -> tuple[list[str], list[str]]:
    """Add [ignore] to lines of `existing` that match (in order) the ignored lines of `wanted`."""
    out = list(existing)
    changes = []
    pos = 0
    for w in wanted:
        ig, wait, body = split(w.replace("# OFF: ", "", 1))
        if not ig:
            continue
        off = w.startswith("# OFF: ")
        for k in range(pos, len(out)):
            cur = out[k]
            cur_off = cur.startswith("# OFF: ")
            cur_body = cur[7:] if cur_off else cur
            if split(cur_body)[0]:
                continue
            if cur_body.strip() == body.strip() and cur_off == off:
                out[k] = ("# OFF: " if off else "") + join(cur_body.strip(), True, wait)
                changes.append(f"  line {k + 1}: {out[k]}")
                pos = k + 1
                break
    return out, changes


def main(apply: bool) -> None:
    imported = json.load(open(os.path.join(ROOT, "data/testsigma_exports/imported.json")))
    flows = set(imported.get("flows") or [])
    store_path = os.path.join(ROOT, "data/reusable_steps.json")
    groups_store = json.load(open(store_path))
    total = 0
    seen: set[str] = set()
    for f in sorted(glob.glob(os.path.join(ROOT, "data/testsigma_exports/run_*/*.json"))):
        try:
            bundle = json.load(open(f))
        except (OSError, ValueError):
            continue
        if not isinstance(bundle, dict) or "steps" not in bundle:
            continue
        try:
            res = tp.convert(bundle)
        except Exception as e:  # noqa: BLE001
            print(f"skip {os.path.basename(f)}: {e}")
            continue
        name = tp.slug(bundle.get("name", ""))
        path = os.path.join(ROOT, "flows", name + ".flow")
        if name in flows and os.path.exists(path) and name not in seen:
            seen.add(name)
            cur = open(path, encoding="utf-8").read().splitlines()
            new, ch = _mark(cur, res["flow"].splitlines())
            if ch:
                total += len(ch)
                print(f"{name}.flow — {len(ch)} step(s)")
                print("\n".join(ch))
                if apply:
                    open(path, "w", encoding="utf-8").write("\n".join(new) + "\n")
        for g, lines in (res.get("groups") or {}).items():
            if g in groups_store and g not in seen:
                seen.add(g)
                cur = groups_store[g].get("steps") or []
                new, ch = _mark(cur, lines)
                if ch:
                    total += len(ch)
                    print(f"step group {g} — {len(ch)} step(s)")
                    print("\n".join(ch))
                    if apply:
                        groups_store[g]["steps"] = new
    if apply and total:
        tmp = store_path + ".tmp"
        json.dump(groups_store, open(tmp, "w"), indent=2, ensure_ascii=False)
        os.replace(tmp, store_path)
    print(f"\n{'Applied' if apply else 'Would mark'} {total} step(s).")


if __name__ == "__main__":
    main("--apply" in sys.argv)
