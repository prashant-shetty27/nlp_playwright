"""
core/run_insights.py — what a plan run MEANS, not just what failed.

cluster(items)   groups failed items by root cause: the same element, the same
                 bot-reply family, the same error family, the same page — so a
                 report shows 6 causes instead of 33 rows. Each cluster carries a
                 kind guess (product-defect / test-problem / data-change /
                 environment / flaky / known) and a suggested action.
history(name)    pass rate and duration over the recent reports of one test case.
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics

from config.settings import DATA_DIR

LOGS_DIR = os.path.join(DATA_DIR, "logs")

#: Error families, in order of precedence.
_FAMILIES = [
    ("environment", r"HTTPConnectionPool|Max retries exceeded|ECONNREFUSED|net::ERR_|getaddrinfo|Name or service not known"),
    ("product-reply", r"\$\{bot_reply\} is"),
    ("login-gate", r"login_gate|rfq-login-gate"),
    ("not-visible", r"did not become visible|did not appear after|did not come into view|Visibility assertion failed|not visible after"),
    ("missing-element", r"Element broken and no ML DNA|Locator .* not found"),
    ("wrong-text", r"expected to contain text|Match Failed|does not match|expected it to match|repeats"),
    ("input", r"Could not put|read-only|covered by an overlay"),
    ("timeout", r"Timeout \d+ms exceeded|timed out"),
]


def _family(err: str) -> str:
    for name, rx in _FAMILIES:
        if re.search(rx, err or "", re.I | re.S):
            return name
    return "other"


def _element(text: str) -> str:
    m = re.search(r"'([a-z_][a-z0-9_]*)'", text or "")
    return m.group(1) if m else ""


def _reply_family(err: str) -> str:
    """The shape of a bot reply, so 'couldn't find … rat glue pad' and
    'couldn't find … ammonium nitrate' land in one cluster."""
    m = re.search(r"\$\{bot_reply\} is ['\"](.{0,60})", err or "", re.S)
    if not m:
        return ""
    t = m.group(1).lower()
    if "find any results" in t or "koi results nahi" in t:
        return "no-results reply"
    if "just to confirm" in t or "are you looking for" in t:
        return "substitute suggestion"
    if "campaign" in t or "help you source" in t or "how many" in t or "what " in t:
        return "campaign started"
    return "other reply"


def cluster(items: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for it in items:
        if it.get("status") != "failed":
            continue
        ff = it.get("first_failure") or ""
        m = re.match(r"line (\d+): (.*?) — (.*)", ff, re.S)
        step, err = (m.group(2), m.group(3)) if m else ("", ff)
        fam = _family(err)
        el = _element(err) or _element(step)
        if it.get("known"):
            key = ("known", it["known"].get("id", ""))
            title = f"Known: {it['known'].get('title', '')}"
            kind = it["known"].get("kind", "known")
        elif fam == "product-reply":
            rf = _reply_family(err)
            key = ("reply", rf)
            title = f"Chat bot gave a '{rf}' where the test expected otherwise"
            kind = "product-defect"
        elif fam == "environment":
            host = re.search(r"host='([^']+)'", err)
            key = ("env", host.group(1) if host else "network")
            title = f"Unreachable: {host.group(1) if host else 'network'}"
            kind = "environment"
        elif fam == "login-gate":
            key = ("login", "")
            title = "Chat login gate still shown (shared test login / parallel run)"
            kind = "test-problem"
        elif fam in ("not-visible", "missing-element"):
            key = ("element", el)
            title = f"'{el}' not found / not visible" if el else "Element not visible"
            kind = "site-change-or-test"
        elif fam == "wrong-text":
            key = ("text", el or step[:40])
            title = f"Wrong content at '{el}'" if el else f"Wrong content: {step[:50]}"
            kind = "product-or-data"
        elif fam == "input":
            key = ("input", el)
            title = f"Could not type into '{el}'"
            kind = "product-or-test"
        else:
            key = ("other", step[:40])
            title = step[:60] or "Failure"
            kind = "unknown"
        g = groups.setdefault(key, {"title": title, "kind": kind, "family": fam, "element": el,
                                    "cases": [], "browsers": set(), "sample_error": err[:300].replace("\n", " ")})
        g["cases"].append(it.get("test_case", ""))
        g["browsers"].add((it.get("device") or {}).get("browser_identity", "default"))
    out = []
    for g in groups.values():
        g["browsers"] = sorted(g["browsers"])
        g["count"] = len(g["cases"])
        g["action"] = _action(g)
        out.append(g)
    out.sort(key=lambda g: (-g["count"], g["title"]))
    return out


def _action(g: dict) -> str:
    k = g["kind"]
    if k == "product-defect":
        return "Raise / update the Jira defect; add to Known issues so the next run reports it as known."
    if k == "environment":
        return "Run these on a VPN plan, or fix reachability — not a product issue."
    if k == "test-problem":
        return "Fix the test / plan setting (e.g. serial login) and re-run failed only."
    if k == "site-change-or-test":
        return ("Open the screenshot: if the element is gone from live, it is a site/data change (register it); "
                "if it is there but was not reached, fix the scroll / popup handling.")
    if k == "product-or-data":
        return "Compare expected vs actual on the screenshot: wrong data → fix test data; wrong product → raise."
    if k.startswith("known"):
        return "Nothing new — tracked in Known issues."
    return "Review the screenshot."


def history(test_case: str, limit: int = 20) -> dict:
    """Recent reports of one test case: pass rate, durations, first failing step trend."""
    runs = []
    for f in sorted(glob.glob(os.path.join(LOGS_DIR, f"report_{test_case}_*.json")))[-limit:]:
        try:
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        if d.get("testplan") != test_case:
            continue
        sm = d.get("summary") or {}
        ff = next((r for r in d.get("results") or [] if str(r.get("status", "")).lower() == "failed"), None)
        runs.append({"when": d.get("started_at", ""), "passed": not sm.get("failed") and sm.get("total"),
                     "total": sm.get("total", 0), "failed": sm.get("failed", 0),
                     "duration_s": (d.get("timing") or {}).get("total_s"),
                     "first_failure": (ff or {}).get("test_name", "")[:90],
                     "browser": (d.get("device") or {}).get("browser_identity", ""),
                     "run_type": d.get("run_type", "")})
    n = len(runs)
    p = sum(1 for r in runs if r["passed"])
    durs = [r["duration_s"] for r in runs if r.get("duration_s")]
    ff_counts: dict[str, int] = {}
    for r in runs:
        if r["first_failure"]:
            ff_counts[r["first_failure"]] = ff_counts.get(r["first_failure"], 0) + 1
    streak = 0
    for r in reversed(runs):
        if r["passed"]:
            break
        streak += 1
    return {"runs": n, "passed": p, "pass_rate": round(100 * p / n) if n else None,
            "median_duration_s": round(statistics.median(durs), 1) if durs else None,
            "fail_streak": streak,
            "top_failing_step": max(ff_counts.items(), key=lambda x: x[1]) if ff_counts else None,
            "recent": runs[-10:]}
