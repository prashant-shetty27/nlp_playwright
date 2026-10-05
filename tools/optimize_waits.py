"""
tools/optimize_waits.py — replace guessed fixed sleeps with condition waits.

    python -m tools.optimize_waits --suite suites/b2b_mobilesite_regression.json          # dry run
    python -m tools.optimize_waits --suite ... --apply                                    # write

Engine facts the rules rely on (execution/action_service.py):
  * open URL            waits for domcontentloaded only
  * click X             waits 5 s for X, then heals
  * verify element X is visible / wait until element X is visible   poll up to 15 s
  * refresh page        waits for the load event
  * wait for page to load   load event (max 30 s) + up to 3 s network quiet

Rules for `wait N seconds` (looking at the step before and after it):
  R1  next step needs element X (click / type / store / verify / scroll to X)
        next is "verify element X is visible"  -> remove (the check itself waits 15 s)
        otherwise                              -> "wait until element X is visible"
  R2  next or previous step is already a condition wait ("wait until …",
      "wait for page to load", "… until X is visible") -> remove
  R3  previous step is "refresh page" -> remove (refresh waits for load)
  R4  right after "open URL" and the next step names no element: wait for the
      page's own landmark (catalogue / PDP / result page) or "wait for page to load"
  R5  next step is a call: use the group's first element, else keep
  R6  last step of the test case / group -> remove
  R7  two fixed waits in a row -> one
  Kept on purpose: before a negative check (not visible / does not contain),
  before an optional step (click if visible, [ignore]), between scrolls/swipes
  (lazy loading), after typing (autosuggest), before opening another URL, and
  after OTP / login / submit steps (nothing on the page to wait for).
  Scroll ladders (3+ fixed scrolls) right before a "scroll/swipe until X is
  visible" step are removed (the until-step does the searching).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

WAIT = re.compile(r"^\s*wait\s+(\d+(?:\.\d+)?)\s*seconds?\s*$", re.I)
COND = re.compile(r"^\s*(wait\s+until\b|wait\s+for\s+(page|element)\b|wait\s+page\s+load)|"
                  r"\buntil\s+(element\s+)?[a-z_]\w*\s+(is\s+)?visible", re.I)
NEG = re.compile(r"\b(not\s+visible|is\s+not|does\s+not|not\s+present|was\s+not|is\s+hidden)\b", re.I)
OPTIONAL = re.compile(r"^\s*\[ignore|\bif\s+visible\b", re.I)
DELIBERATE_PREV = re.compile(r"\b(otp|login|log\s*in|sign\s*in|submit|send\s+enquiry|payment|api\s|"
                             r"upload|download|fetch|logout)\b", re.I)
SCROLLISH = re.compile(r"^\s*(scroll|swipe)\b(?!.*\buntil\b)(?!\s+to\b)", re.I)
TYPE = re.compile(r"^\s*(type|enter|fill)\b", re.I)
OPEN = re.compile(r"^\s*(open|navigate\s+to|go\s+to)\s+(\S+)", re.I)
CALL = re.compile(r"^\s*call\s+(.+?)\s*$", re.I)
UNTIL_STEP = re.compile(r"^\s*(?:\[ignore[^\]]*\]\s*)?(?:scroll|swipe)\b.*\buntil\s+(?:element\s+)?"
                        r"[a-z_]\w*\s+(?:is\s+)?visible", re.I)
SCROLL_STEP = re.compile(r"^\s*(scroll|swipe)\s+(down|up|by)\b(?!.*\buntil\b)", re.I)

_TGT = [
    re.compile(r"^\s*(?:js\s+)?(?:click|tap|double\s+click|hover(?:\s+over)?|clear|scroll\s+to)"
               r"(?:\s+element)?\s+([a-z_]\w*)\s*$", re.I),
    re.compile(r'^\s*(?:type|enter)\s+"[^"]*"\s+into\s+([a-z_]\w*)\s*$', re.I),
    re.compile(r"^\s*store\s+(?:text|value|count|attribute\s+\S+)\s+of\s+([a-z_]\w*)\b", re.I),
    re.compile(r"^\s*verify\s+(?:that\s+)?element\s+([a-z_]\w*)\b", re.I),
    re.compile(r"^\s*verify\s+(?:the\s+)?text\s+of\s+([a-z_]\w*)\b", re.I),
    re.compile(r"^\s*wait\s+until\s+element\s+([a-z_]\w*)\s+is\s+visible", re.I),
    re.compile(r'^\s*select\s+option\s+"[^"]*"\s+in\s+([a-z_]\w*)\s*$', re.I),
]

#: A page's landmark — the element that says "this page has rendered".
LANDMARKS = [
    (re.compile(r"_BZDET", re.I), "b2b_catalogue_company_name"),
    (re.compile(r"/pid-\d+", re.I), "pdp_main_image"),
]


#: Steps that read an element's CONTENT. A pause before one is usually waiting
#: for that content to change (a city name updating from Pune to Delhi) — the
#: element is already visible, so a visibility wait would not wait at all.
CONTENT = re.compile(r"^\s*(verify\s+(?:that\s+)?(?:element\s+)?[a-z_]\w*\s+(?!is\s+visible\s*$)"
                     r"(?:contains|has|equals|text|matches|does|displays|count|value|attribute)\b|"
                     r"verify\s+(?:the\s+)?text\s+of\b|store\s+(?:text|value|count|attribute)\b|"
                     r"verify\s+text\b|verify\s+stored\b)", re.I)


def target(step: str) -> str:
    if OPTIONAL.search(step) or NEG.search(step):
        return ""
    for rx in _TGT:
        m = rx.match(step)
        if m:
            return m.group(1)
    return ""


def wait_for(el: str) -> str:
    return f"wait until element {el} is visible"


def load_groups() -> dict:
    with open(os.path.join(BASE, "data", "reusable_steps.json"), encoding="utf-8") as f:
        return json.load(f)


def group_first_target(name: str, groups: dict, depth: int = 0) -> str:
    g = groups.get(name) or next((v for k, v in groups.items() if k.lower() == name.lower()), None)
    if not g or depth > 4:
        return ""
    for s in (g.get("steps") if isinstance(g, dict) else g) or []:
        s = s.strip()
        if not s or s.startswith("#"):
            continue
        m = CALL.match(s)
        if m:
            return group_first_target(m.group(1), groups, depth + 1)
        if WAIT.match(s):
            continue
        return "" if CONTENT.match(s) else target(s)
    return ""


def _collapse_runs(lines: list[str]) -> tuple[list[str], list[str]]:
    """Back-to-back fixed waits (often left where switched-off steps used to be)
    become ONE wait of the same total, so the rules judge the whole pause once."""
    live = [i for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("#")]
    drop, notes = set(), []
    k = 0
    while k < len(live):
        if WAIT.match(lines[live[k]].strip()):
            j = k
            while j + 1 < len(live) and WAIT.match(lines[live[j + 1]].strip()):
                j += 1
            if j > k:
                total = sum(float(WAIT.match(lines[live[q]].strip()).group(1)) for q in range(k, j + 1))
                first = live[k]
                indent = lines[first][:len(lines[first]) - len(lines[first].lstrip())]
                lines[first] = f"{indent}wait {total:g} seconds" + ("\n" if lines[first].endswith("\n") else "")
                drop.update(live[q] for q in range(k + 1, j + 1))
                notes.append(f"L{first + 1}-{live[j] + 1}: {j - k + 1} fixed waits in a row -> one 'wait {total:g} seconds'")
            k = j + 1
        else:
            k += 1
    return [l for i, l in enumerate(lines) if i not in drop], notes


def group_starts_with_scroll(name: str, groups: dict, depth: int = 0) -> bool:
    g = groups.get(name) or next((v for k, v in groups.items() if k.lower() == name.lower()), None)
    if not g or depth > 4:
        return False
    for s in (g.get("steps") if isinstance(g, dict) else g) or []:
        s = s.strip()
        if not s or s.startswith("#") or WAIT.match(s):
            continue
        m = CALL.match(s)
        if m:
            return group_starts_with_scroll(m.group(1), groups, depth + 1)
        return bool(SCROLLISH.match(s))
    return False


def optimise(lines: list[str], groups: dict, is_group: bool = False) -> tuple[list[str], list[str]]:
    """Return (new raw lines, change notes). Comments / OFF lines are kept as they are."""
    lines, run_notes = _collapse_runs(list(lines))
    live = [i for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("#")]
    txt = {i: lines[i].strip() for i in live}
    new: dict[int, str | None] = {}           # raw index -> replacement (None = remove)
    notes: list[str] = list(run_notes)

    # scroll ladders right before an until-step
    k = 0
    while k < len(live):
        j = k
        scrolls = 0
        while j < len(live) and (SCROLL_STEP.match(txt[live[j]]) or WAIT.match(txt[live[j]])):
            scrolls += bool(SCROLL_STEP.match(txt[live[j]]))
            j += 1
        if scrolls >= 3 and j < len(live) and UNTIL_STEP.match(txt[live[j]]):
            for q in range(k, j):
                new[live[q]] = None
            notes.append(f"L{live[k] + 1}-{live[j - 1] + 1}: scroll ladder removed (L{live[j] + 1} scrolls until visible)")
        k = max(j, k + 1)

    for pos, i in enumerate(live):
        if i in new:
            continue
        m = WAIT.match(txt[i])
        if not m:
            continue
        secs = float(m.group(1))
        prev = next((txt[live[q]] for q in range(pos - 1, -1, -1) if new.get(live[q], 1) is not None), "")
        nxt_i = next((live[q] for q in range(pos + 1, len(live)) if new.get(live[q], 1) is not None), None)
        nxt = txt[nxt_i] if nxt_i is not None else ""
        why = rep = ""
        drop = False
        if not nxt:
            if is_group:
                continue          # a group's last wait prepares the CALLER's next step
            drop, why = True, "R6 last step"
        elif SCROLLISH.match(nxt) or (CALL.match(nxt) and group_starts_with_scroll(CALL.match(nxt).group(1), groups)):
            continue                  # scroll-triggered content needs the page's scripts ready
        elif COND.search(nxt) and not NEG.search(nxt):
            drop, why = True, "R2 next step is a condition wait"
        elif COND.search(prev) and not SCROLLISH.match(prev) and not target(nxt) \
                and not NEG.search(nxt) and not OPTIONAL.search(nxt) and not OPEN.match(nxt):
            drop, why = True, "R2 previous step already waited for its condition"
        elif re.match(r"^\s*refresh\b", prev, re.I):
            drop, why = True, "R3 refresh already waits for load"
        elif re.match(r"^\s*(refresh|delete\s+all\s+cookies)\b", nxt, re.I):
            if secs > 2:
                rep, why = "wait 2 seconds", "R8 the page reloads next; long pause trimmed"
            else:
                continue
        elif NEG.search(nxt) or OPTIONAL.search(nxt) or SCROLLISH.match(prev) or SCROLLISH.match(nxt) \
                or TYPE.match(prev) or OPEN.match(nxt) or re.match(r"^\s*end\b", nxt, re.I):
            continue
        elif DELIBERATE_PREV.search(prev.replace("_", " ")) and not OPEN.match(prev):
            continue
        elif CONTENT.match(nxt):
            continue                  # waiting for content to change, not to appear
        else:
            t = target(nxt)
            if not t and CALL.match(nxt):
                t = group_first_target(CALL.match(nxt).group(1), groups)
            if t:
                if re.match(rf"^\s*verify\s+element\s+{re.escape(t)}\s+is\s+visible\s*$", nxt, re.I):
                    drop, why = True, "R1 the check waits 15 s itself"
                else:
                    rep, why = wait_for(t), "R1 wait for the element the next step uses"
            elif OPEN.match(prev):
                url = OPEN.match(prev).group(2)
                lm = next((el for rx, el in LANDMARKS if rx.search(url)), "")
                rep = wait_for(lm) if lm else "wait for page to load"
                why = "R4 after open: page landmark" if lm else "R4 after open"
            else:
                continue
        if drop:
            new[i] = None
            notes.append(f"L{i + 1}: '{txt[i]}' removed ({why})")
        elif rep:
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            new[i] = indent + rep + ("\n" if lines[i].endswith("\n") else "")
            notes.append(f"L{i + 1}: '{txt[i]}' -> '{rep}' ({why})")
    out = [new.get(i, l) for i, l in enumerate(lines)]
    return [l for l in out if l is not None], notes


def suite_flows(suite: str) -> list[str]:
    with open(os.path.join(BASE, suite), encoding="utf-8") as f:
        s = json.load(f)
    return [os.path.basename(sc.get("script") if isinstance(sc, dict) else sc).replace(".flow", "")
            for sc in s.get("scripts", [])]


def used_groups(flows: list[str], groups: dict) -> list[str]:
    seen: list[str] = []
    todo = []
    for n in flows:
        for l in open(os.path.join(BASE, "flows", n + ".flow"), encoding="utf-8"):
            m = CALL.match(l.strip())
            if m:
                todo.append(m.group(1))
    while todo:
        g = todo.pop()
        key = g if g in groups else next((k for k in groups if k.lower() == g.lower()), None)
        if not key or key in seen:
            continue
        seen.append(key)
        for s in (groups[key].get("steps") if isinstance(groups[key], dict) else groups[key]) or []:
            m = CALL.match(s.strip())
            if m:
                todo.append(m.group(1))
    return seen


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
    gchanged = False
    for g in used_groups(flows, groups):
        body = groups[g]
        steps = body.get("steps") if isinstance(body, dict) else body
        out, notes = optimise([s + "\n" for s in steps], groups, is_group=True)
        if notes:
            total += len(notes)
            print(f"== step group {g}")
            for x in notes:
                print("   ", x)
            if a.apply:
                new_steps = [l.rstrip("\n") for l in out]
                if isinstance(body, dict):
                    body["steps"] = new_steps
                    body["step_count"] = len(new_steps)
                else:
                    groups[g] = new_steps
                gchanged = True
    if a.apply and gchanged:
        p = os.path.join(BASE, "data", "reusable_steps.json")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(groups, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    print(f"{total} change(s){' applied' if a.apply else ' (dry run)'}")


if __name__ == "__main__":
    main()
