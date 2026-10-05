"""
tools/optimize_scrolls.py — replace guessed fixed scrolls with "scroll until".

    python -m tools.optimize_scrolls --suite suites/b2b_mobilesite_regression.json [--apply]

A run of fixed scrolls/swipes down ("scroll down 2000", "swipe bottom to top",
with fixed waits between them) is a guess at where something is. It is
replaced by a step that scrolls until the thing the NEXT step needs is on screen:

  next step uses element X (click / type / verify / store / scroll to / wait until)
        -> scroll until element X visible, scroll by 600 pixels, scroll count N, scroll wait 1
           (swipe runs -> swipe bottom to top until element X is visible, max N times, wait 1)
  next step is  verify text "T" on page
        -> scroll until text "T" visible, scroll count N, scroll wait 1
  next step already scrolls/swipes until something -> the run is removed
  optional next step ([ignore] / if visible) -> the until-step is [ignore 30s] too
  "scroll to X" / "wait until element X is visible" right after become redundant -> removed

Kept (the scroll itself is the point): before a negative check (scrolling PAST a
section), inside a repeat loop, before an if (scroll-triggered popup), before a
call whose first step names no element, and upward scrolls.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from tools.optimize_waits import (CALL, NEG, WAIT, load_groups, suite_flows,  # noqa: E402
                                  target, used_groups)

STATIC = re.compile(r"^\s*(?:scroll\s+down\s+\d+|scroll\s+by\s+\d+|swipe\s+bottom\s+to\s+(?:top|middle)|"
                    r"swipe\s+middle\s+to\s+top|scroll\s+down)\s*$", re.I)
UNTIL = re.compile(r"\buntil\b", re.I)
IGNORE = re.compile(r"^\s*\[ignore[^\]]*\]\s*", re.I)
TEXT_ON_PAGE = re.compile(r'^\s*verify\s+text\s+"([^"]+)"\s+on\s+page\s*$', re.I)


def _first_group_step(name: str, groups: dict, depth: int = 0) -> str:
    g = groups.get(name) or next((v for k, v in groups.items() if k.lower() == name.lower()), None)
    if not g or depth > 4:
        return ""
    for s in (g.get("steps") if isinstance(g, dict) else g) or []:
        s = s.strip()
        if not s or s.startswith("#") or WAIT.match(s):
            continue
        m = CALL.match(s)
        return _first_group_step(m.group(1), groups, depth + 1) if m else s
    return ""


def optimise(lines: list[str], groups: dict) -> tuple[list[str], list[str]]:
    live = [i for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("#")]
    txt = {i: lines[i].strip() for i in live}
    new: dict[int, str | None] = {}
    notes: list[str] = []
    k = 0
    while k < len(live):
        if not STATIC.match(txt[live[k]]):
            k += 1
            continue
        def _optional(t: str) -> bool:
            return (bool(IGNORE.match(t)) or bool(re.search(r"\bif\s+visible\b", t, re.I))) \
                and not UNTIL.search(t) and not re.match(r"^\s*(\[ignore[^\]]*\]\s*)?scroll\s+to\b", t, re.I)
        j = k
        swipes = scrolls = 0
        dist = 0
        keep_opt: list[int] = []         # optional steps (close a popup if it shows) — kept, after the until-step
        while j < len(live) and (STATIC.match(txt[live[j]]) or WAIT.match(txt[live[j]]) or _optional(txt[live[j]])):
            t = txt[live[j]]
            if STATIC.match(t):
                scrolls += 1
                swipes += t.lower().startswith("swipe")
                m = re.search(r"(\d+)", t)
                dist = max(dist, int(m.group(1))) if m and "down" in t.lower() else dist + 700
            elif _optional(t):
                keep_opt.append(live[j])
            j += 1
        while j - 1 > k and (WAIT.match(txt[live[j - 1]]) or live[j - 1] in keep_opt) and not STATIC.match(txt[live[j - 1]]):
            if live[j - 1] in keep_opt:
                break
            j -= 1
        run = [i for i in live[k:j] if i not in keep_opt]
        q = j
        while q < len(live) and WAIT.match(txt[live[q]]):
            q += 1
        nxt_i = live[q] if q < len(live) else None
        nxt = txt[nxt_i] if nxt_i is not None else ""
        count = max(10, min(25, dist // 600 + 6))
        label = f"L{run[0] + 1}" + (f"-{run[-1] + 1}" if len(run) > 1 else "")
        rep = ""
        drop_next = False
        body = IGNORE.sub("", nxt)
        optional = bool(IGNORE.match(nxt)) or bool(re.search(r"\bif\s+visible\b", nxt, re.I))
        if not nxt or NEG.search(nxt) or re.match(r"^\s*(if|elif|else|end|repeat|for)\b", nxt, re.I):
            k = j
            continue
        if UNTIL.search(nxt) and re.match(r"^\s*(\[ignore[^\]]*\]\s*)?(scroll|swipe)\b", nxt, re.I):
            for i in run:
                new[i] = None
            notes.append(f"{label}: fixed scrolls removed — the next step already scrolls until it finds its target")
            k = j
            continue
        m = TEXT_ON_PAGE.match(body)
        el = ""
        if m:
            rep = f'scroll until text "{m.group(1)}" visible, scroll count {count}, scroll wait 1'
        else:
            mm = re.match(r"^\s*(?:scroll\s+to|wait\s+until\s+element)\s+([a-z_]\w*)(?:\s+is\s+visible)?\s*$", body, re.I)
            if mm:
                el = mm.group(1)
                drop_next = True
            else:
                el = target(re.sub(r"\bif\s+visible\s+", "", body, flags=re.I))
                if not el and CALL.match(body):
                    first = _first_group_step(CALL.match(body).group(1), groups)
                    el = target(first) if first and not NEG.search(first) else ""
            if el:
                rep = (f"swipe bottom to top until element {el} is visible, max {count} times, wait 1"
                       if swipes == scrolls else
                       f"scroll until element {el} visible, scroll by 600 pixels, scroll count {count}, scroll wait 1")
        if not rep:
            k = j
            continue
        if optional:
            rep = "[ignore 30s] " + rep
        first = run[0]
        indent = lines[first][:len(lines[first]) - len(lines[first].lstrip())]
        new[first] = indent + rep + ("\n" if lines[first].endswith("\n") else "")
        for i in run[1:]:
            new[i] = None
        # the fixed waits we kept between the run and the next step are now pointless
        for w in live[j:q]:
            new[w] = None
        if drop_next and not optional:
            new[nxt_i] = None
        notes.append(f"{label}: {len(run)} fixed scroll step(s) -> '{rep}'"
                     + (f" (and '{nxt}' removed)" if drop_next and not optional else ""))
        k = q
    out = [new.get(i, l) for i, l in enumerate(lines)]
    return [l for l in out if l is not None], notes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    groups = load_groups()
    flows = suite_flows(a.suite)
    total = 0
    for n in flows:
        p = os.path.join(BASE, "flows", n + ".flow")
        lines = open(p, encoding="utf-8").readlines()
        out, notes = optimise(lines, groups)
        if notes:
            total += len(notes)
            print(f"== {n}")
            for x in notes:
                print("   ", x)
            if a.apply:
                with open(p, "w", encoding="utf-8") as f:
                    f.writelines(out)
    changed = False
    for g in used_groups(flows, groups):
        body = groups[g]
        steps = body.get("steps") if isinstance(body, dict) else body
        out, notes = optimise([s + "\n" for s in steps], groups)
        if notes:
            total += len(notes)
            print(f"== step group {g}")
            for x in notes:
                print("   ", x)
            if a.apply:
                ns = [l.rstrip("\n") for l in out]
                if isinstance(body, dict):
                    body["steps"], body["step_count"] = ns, len(ns)
                else:
                    groups[g] = ns
                changed = True
    if a.apply and changed:
        p = os.path.join(BASE, "data", "reusable_steps.json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(groups, f, indent=2, ensure_ascii=False)
        os.replace(p + ".tmp", p)
    print(f"{total} change(s){' applied' if a.apply else ' (dry run)'}")


if __name__ == "__main__":
    main()
