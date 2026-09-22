#!/usr/bin/env python3
"""
tools/testsigma_import.py — migrate a Testsigma test case into this tool.

    python tools/testsigma_import.py <testsigma_result.xml> [--platform mobilesite]
                                     [--group <locator group>] [--dry-run] [--overwrite]

Input : the <testCase> XML you get from a Testsigma run result page
        (copy it into data/testsigma_exports/<name>.xml first).
Output: three things, all in the places the UI already reads from:

  1. flows/<test case name>.flow          one step per line in THIS tool's grammar,
                                          with "# Platform: <platform>" on top, so it
                                          shows up at /platform/<platform>?flow=<name>
  2. data/locators_manual.json            every element the test refers to, as a
                                          snake_case locator inside one group
                                          (default group: ts_<test case name>)
  3. data/reusable_steps.json             Testsigma step groups (e.g.
                                          PS_Touch_Consent_Popup) as reusable step
                                          groups, called from the flow with "call <name>"

Testsigma NLP -> tool grammar mapping is the MAPPING table below. Anything without
a mapping is written into the flow as a commented "# UNSUPPORTED:" line so nothing
is silently dropped, and listed in the summary. The flow is linted with
tools/flow_lint.py at the end, so what is written is known to parse.

Control flow (Testsigma exports it flattened):
  While element X is not visible  +  Swipe ...   ->  scroll until text "<X's text>" visible
  If <cond>  +  Tap on Y                         ->  click if visible Y
  If <cond>  +  <anything else>                  ->  commented, needs a manual decision
  Tap on X  +  Enter data V on focused element   ->  type "V" into X
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from locators.manager import add_locator, load_locators, normalise_name  # noqa: E402
from core import reusable_steps  # noqa: E402

SEED_FILE = os.path.join(BASE_DIR, "tools", "testsigma_seed_locators.json")

# ---------------------------------------------------------------------------
# Testsigma NLP -> tool grammar
# Each entry: (compiled regex, handler(match, ctx) -> list[str] | None)
# A handler returns the tool-grammar lines; None means unsupported.
# ctx.loc(name) registers an element and returns its snake_case locator name.
# ---------------------------------------------------------------------------
_ITER = re.compile(r"^Iteration #\d+$", re.I)
_XPATH_RX = re.compile(r'XPATH:(.*?)"\s*(?:</b>|\s)', re.S)


def _q(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


MAPPING = [
    (r"^navigate to\s+(?P<url>\S+)$", lambda m, c: [f"open {m['url']}"]),
    (r"^delete all (?:local storage )?cookies.*$", lambda m, c: ["delete all cookies"]),
    (r"^wait for\s+(?P<n>\d+)\s*seconds?$", lambda m, c: [f"wait {m['n']} seconds"]),
    (r"^tap on the back in the browser$", lambda m, c: ["press back"]),
    (r"^refresh the (?:current )?page$", lambda m, c: ["refresh page"]),
    # native context switches have no meaning in Playwright mobile emulation
    (r"^switch to (?:native view context|context with name .+)$", lambda m, c: []),
    (r"^wait until the element\s+(?P<el>.+?)\s+is\s+(?:clickable|visible|present|enabled)$",
     lambda m, c: [f"wait until element {c.loc(m['el'])} is visible"]),
    (r"^wait until the text\s+(?P<t>.+?)\s+is present on the current page$",
     lambda m, c: [f"verify text {_q(m['t'])} on page"]),
    (r"^verify that the current page displays text\s+(?P<t>.+)$",
     lambda m, c: [f"verify text {_q(m['t'])} on page"]),
    (r"^verify that the current page does not display text\s+(?P<t>.+)$",
     lambda m, c: [f"verify page does not contain {_q(m['t'])}"]),
    (r"^verify that the current page displays an element\s+(?P<el>.+)$",
     lambda m, c: [f"verify element {c.loc(m['el'])} is visible"]),
    (r"^verify that the current page does not display an element\s+(?P<el>.+)$",
     lambda m, c: [f"verify element {c.loc(m['el'])} is not visible"]),
    (r"^enter data\s+(?P<v>.+?)\s+on focused element$", lambda m, c: None),   # merged with previous Tap
    (r"^enter\s+(?P<v>.+?)\s+in the\s+(?P<el>.+?)(?:\s+field)?$",
     lambda m, c: [f"type {_q(m['v'])} into {c.loc(m['el'])}"]),
    (r"^click on the element\s+(?P<el>.+?)\s+using javascript executor$",
     lambda m, c: [f"js click {c.loc(m['el'])}"]),
    (r"^tap on the element with text\s+(?P<t>.+?)(?P<opt>\s+if visible)?$",
     lambda m, c: ["click if visible " + c.loc(m['t'] + ' text', xpath="//*[normalize-space()='" + m['t'] + "']")]),
    (r"^tap on\s+(?P<el>.+?)\s+if (?:visible|present)$",
     lambda m, c: [f"click if visible {c.loc(m['el'])}"]),
    (r"^tap on\s+(?P<el>.+?)$", lambda m, c: [f"click {c.loc(m['el'])}"]),
    (r"^scroll the window to\s+(?P<n>-?\d+)\s+offset vertically$",
     lambda m, c: [f"scroll down {m['n']}"]),
    (r"^scroll to the element\s+(?P<el>.+?)\s+into view$",
     lambda m, c: [f"scroll to {c.loc(m['el'])}"]),
    (r"^swipe bottom to top(?: in the screen)?$", lambda m, c: ["scroll down 600"]),
    (r"^swipe top to bottom(?: in the screen)?$", lambda m, c: ["scroll up 600"]),
]
_COMPILED = [(re.compile(p, re.I), h) for p, h in MAPPING]

_IF = re.compile(r"^if\s+(?P<cond>.+)$", re.I)
_WHILE = re.compile(r"^while element\s+(?P<el>.+?)\s+is not visible(?: on the page)?$", re.I)
_TAP = re.compile(r"^tap on\s+(?P<el>.+?)(?:\s+if visible)?$", re.I)
_ENTER_FOCUSED = re.compile(r"^enter data\s+(?P<v>.+?)\s+on focused element$", re.I)


class Ctx:
    """Collects the elements a test refers to and their locators."""

    def __init__(self, group: str, seed: dict):
        self.group = group
        self.seed = {self._k(k): v for k, v in seed.items()}
        self.elements: dict[str, dict] = {}   # snake name -> {"testsigma": name, "xpath": ..., "source": ...}
        self.unsupported: list[str] = []
        self.aliases: dict[str, str] = {}      # name that would have been a duplicate -> name used
        try:
            self.existing = load_locators()
        except Exception:  # noqa: BLE001
            self.existing = {}

    @staticmethod
    def _k(name: str) -> str:
        return " ".join(name.lower().split())

    def loc(self, ts_name: str, xpath: str | None = None, reason: str | None = None) -> str:
        name = normalise_name(ts_name, kind="locator")
        if name in self.elements:
            if xpath and self.elements[name]["source"] == "guess":
                self.elements[name].update(xpath=xpath, source="testsigma")
            return name
        seed = self.seed.get(self._k(ts_name))
        if xpath:
            src = "explicit"
        elif seed:
            xpath, src = seed["value"], "seed"
        else:
            loc_from_reason = _locator_from_reason(reason)
            if loc_from_reason:
                xpath, src = loc_from_reason, "testsigma"
            else:
                t = ts_name.replace("'", "")
                xpath, src = f"//*[contains(normalize-space(),'{t}')]", "guess"
        # Same xpath already registered (in this import or in the locator DB)?
        # The tool refuses duplicate xpaths, so reuse that name instead.
        for other, meta in self.elements.items():
            if meta["xpath"] == xpath:
                self.aliases[name] = other
                return other
        for grp, els in self.existing.items():
            for other, val in els.items():
                v = val.get("custom_xpath") if isinstance(val, dict) else val
                if v == xpath:
                    if other != name:          # same name re-imported is not a merge
                        self.aliases[name] = other
                    return other
        self.elements[name] = {"testsigma": ts_name, "xpath": xpath, "source": src,
                               "native": xpath.startswith("//android.")}
        return name


def _locator_from_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    m = _XPATH_RX.search(html.unescape(reason))
    return m.group(1).strip() if m else None


def _convert_line(nlp: str, ctx: Ctx, reason: str | None = None) -> list[str] | None:
    for rx, handler in _COMPILED:
        m = rx.match(nlp.strip())
        if m:
            # let loc() see the failure reason for a Testsigma-printed XPath
            saved = ctx.loc
            ctx.loc = lambda n, xpath=None, _r=reason, _s=saved: _s(n, xpath=xpath, reason=_r)
            try:
                return handler(m, ctx)
            finally:
                ctx.loc = saved
    return None


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def convert(xml_path: str, platform: str, ctx: Ctx, group_names: set[str]):
    tc = ET.parse(xml_path).getroot()
    steps = [{"nlp": s.get("nlp", "").strip(), "result": s.get("result", ""),
              "reason": s.get("reasonForFailure")} for s in tc.find("steps")]
    name = tc.get("name")

    out = [f"# Platform: {platform}",
           f"# Imported from Testsigma: {name} (run {tc.get('runId')}, {tc.get('testPlan')}, {tc.get('testMachine')})",
           "# Lines starting with '# UNSUPPORTED' or '# IF' need a manual decision.", ""]
    groups_found: dict[str, list[str]] = {}
    i, n = 0, len(steps)

    def emit_plain(step):
        lines = _convert_line(step["nlp"], ctx, step["reason"])
        if lines is None:
            ctx.unsupported.append(step["nlp"])
            out.append(f"# UNSUPPORTED: {step['nlp']}")
        else:
            out.extend(lines)

    while i < n:
        s = steps[i]
        nlp = s["nlp"]
        if _ITER.match(nlp):
            i += 1
            continue

        # --- Tap on X + Enter data on focused element -> type into X ----------
        if i + 1 < n and _TAP.match(nlp) and _ENTER_FOCUSED.match(steps[i + 1]["nlp"]):
            el = _TAP.match(nlp)["el"]
            v = _ENTER_FOCUSED.match(steps[i + 1]["nlp"])["v"]
            out.append(f"type {_q(v)} into {ctx.loc(el, reason=s['reason'])}")
            i += 2
            continue

        # --- While element X is not visible: body = steps between Iteration #1 and #2
        mw = _WHILE.match(nlp)
        if mw:
            i += 1
            body = []
            if i < n and _ITER.match(steps[i]["nlp"]):
                i += 1
                while i < n and not _ITER.match(steps[i]["nlp"]):
                    body.append(steps[i]); i += 1
                while i < n and (_ITER.match(steps[i]["nlp"]) or steps[i]["result"] == "Not Executed"
                                 and steps[i]["nlp"] in {b["nlp"] for b in body}):
                    i += 1
            el = mw["el"]
            seed = ctx.seed.get(ctx._k(el), {})
            text = seed.get("text") or re.sub(r"\s*\d+$|\s*button.*$", "", el, flags=re.I)
            out.append(f"# WHILE {nlp}  ->  replaced by scroll-until-text")
            out.append(f'scroll until text {_q(text)} visible, scroll count 10, scroll wait 1')
            for b in body:
                if _TAP.match(b["nlp"]) and b["nlp"].lower().endswith("if visible"):
                    out.append(f"click if visible {ctx.loc(_TAP.match(b['nlp'])['el'])}")
            continue

        # --- If <cond>: body = the Not Executed steps that follow, or the next step
        mi = _IF.match(nlp)
        if mi:
            i += 1
            body = []
            while i < n and steps[i]["result"] == "Not Executed" and not _ITER.match(steps[i]["nlp"]):
                body.append(steps[i]); i += 1
            if not body and i < n:
                body.append(steps[i]); i += 1
            cond_lines = _convert_line(mi["cond"], ctx) or []
            cond_txt = cond_lines[0] if cond_lines else mi["cond"]
            out.append(f"# IF {mi['cond']}")
            for b in body:
                conv = _convert_line(b["nlp"], ctx, b["reason"]) or []
                if len(conv) == 1 and conv[0].startswith("click ") and not conv[0].startswith("click if visible"):
                    out.append(conv[0].replace("click ", "click if visible ", 1))
                else:
                    ctx.unsupported.append(f"If {mi['cond']} -> {b['nlp']}")
                    out.append(f"# UNSUPPORTED (inside If, condition: {cond_txt}): {b['nlp']}")
            continue

        # --- step group: unknown NLP followed by its flattened steps ------------
        if _convert_line(nlp, ctx) is None and not _IF.match(nlp) and re.match(r"^[A-Za-z0-9_ \-]+$", nlp) and " " not in nlp.strip():
            gname = nlp.strip()
            i += 1
            body = []
            # A group's steps follow it; take steps until the first step that also
            # appears later in the test at top level is not knowable, so take the run
            # of steps that only exist in a known group, or stop at the first step
            # that is not native/consent related. Heuristic: stop when a step is
            # followed by a Tap on a clearly page element. Keep it simple: take the
            # steps until the next 'Tap on' that is not optional/native.
            while i < n:
                st = steps[i]["nlp"]
                low = st.lower()
                if low.startswith(("switch to", "tap on location", "tap on consent", "tap on the element with text consent")):
                    body.append(steps[i]); i += 1
                    continue
                break
            lines = []
            seen = set()
            for b in body:
                l = _convert_line(b["nlp"], ctx, b["reason"])
                if l is None:
                    lines.append(f"# UNSUPPORTED: {b['nlp']}")
                    continue
                for x in l:
                    # inside a consent popup group every click is optional
                    x = re.sub(r"^click (?!if visible)", "click if visible ", x)
                    if x not in seen:
                        seen.add(x); lines.append(x)
            groups_found[gname] = lines
            group_names.add(gname)
            out.append(f"call {gname}")
            continue

        emit_plain(s)
        i += 1

    native = {n for n, m in ctx.elements.items() if m["native"]}

    def scrub(lines):
        res = []
        for l in lines:
            hit = next((n for n in native if re.search(r"\b" + re.escape(n) + r"\b", l)), None)
            if hit and not l.startswith("#"):
                res.append(f"# NATIVE-ONLY (Appium), skipped in {platform}: {l}")
            else:
                res.append(l)
        return res

    out = scrub(out)
    groups_found = {g: scrub(ls) for g, ls in groups_found.items()}
    return name, "\n".join(out) + "\n", groups_found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xml")
    ap.add_argument("--platform", default="mobilesite")
    ap.add_argument("--group", help="locator group name (default ts_<test case>)")
    ap.add_argument("--flow-name", help="flow file name without .flow (default: test case name)")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--overwrite", action="store_true", help="overwrite an existing flow / step group")
    a = ap.parse_args()

    seed = {}
    if os.path.exists(SEED_FILE):
        with open(SEED_FILE, encoding="utf-8") as f:
            seed = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

    tc_name_probe = ET.parse(a.xml).getroot().get("name")
    group = a.group or normalise_name("ts " + tc_name_probe, kind="group")
    ctx = Ctx(group, seed)
    group_names: set[str] = set()
    name, flow_text, groups = convert(a.xml, a.platform, ctx, group_names)
    flow_name = a.flow_name or _slug(name)
    flow_path = os.path.join(BASE_DIR, "flows", flow_name + ".flow")

    print(f"Test case : {name}")
    print(f"Flow      : flows/{flow_name}.flow  ({flow_text.count(chr(10))} lines)")
    print(f"Elements  : {len(ctx.elements)} -> locator group '{group}'")
    print(f"Groups    : {', '.join(groups) or '-'}")
    if ctx.aliases:
        print("Merged (same xpath): " + ", ".join(f"{k} -> {v}" for k, v in ctx.aliases.items()))
    if ctx.unsupported:
        print(f"Unsupported ({len(ctx.unsupported)}) - left as comments in the flow:")
        for u in ctx.unsupported:
            print(f"   - {u}")

    if a.dry_run:
        print("\n" + flow_text)
        for n_, ls in groups.items():
            print(f"--- step group {n_}:\n  " + "\n  ".join(ls))
        print("--- elements:")
        for k, v in ctx.elements.items():
            print(f"  {k:45s} [{v['source']}] {v['xpath']}")
        return 0

    if os.path.exists(flow_path) and not a.overwrite:
        print(f"\nflows/{flow_name}.flow already exists - use --overwrite to replace it.")
        return 1

    # 1. locators
    existing = load_locators()
    added = skipped = 0
    for lname, meta in ctx.elements.items():
        if meta["native"]:
            skipped += 1
            continue
        if lname in existing.get(group, {}):
            skipped += 1
            continue
        if add_locator(group, lname, meta["xpath"]):
            added += 1
        else:
            skipped += 1
    print(f"\nLocators  : {added} added, {skipped} skipped (already present / duplicate xpath / native-only)")

    # 2. step groups
    for gname, lines in groups.items():
        steps = [l for l in lines if l and not l.startswith("#")]
        try:
            reusable_steps.save(gname, steps, overwrite=a.overwrite, platform=a.platform)
            print(f"Step group: '{gname}' saved ({len(steps)} steps)")
        except ValueError as e:
            if str(e).startswith("DUPLICATE:"):
                print(f"Step group: '{gname}' already exists - kept (use --overwrite to replace)")
            else:
                raise

    # 3. flow
    os.makedirs(os.path.dirname(flow_path), exist_ok=True)
    with open(flow_path, "w", encoding="utf-8") as f:
        f.write(flow_text)
    print(f"Flow      : written flows/{flow_name}.flow")

    # 4. lint with the tool's own validator
    lint = os.path.join(BASE_DIR, "tools", "flow_lint.py")
    if os.path.exists(lint):
        print("\n--- flow_lint ---")
        subprocess.run([sys.executable, lint, flow_path, "--platform", a.platform])
    return 0


if __name__ == "__main__":
    sys.exit(main())
