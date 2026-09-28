"""
tools/testsigma_pull.py — bring Testsigma test cases into this portal.

Two stages, both driven from the portal's "Testsigma import" page:

1. PULL  (needs the Testsigma API; runs on the portal machine)
   pull_run(run_id) reads a run's test cases, their steps (/api/v2/test_steps/
   find_all/<id>), every step group they call, and every element they use
   (/api/v2/elements/<id>, which carries the REAL locator). One JSON bundle per
   test case is saved under data/testsigma_exports/run_<run id>/ — nothing in
   the portal changes yet.

2. CONVERT / IMPORT  (offline, from the saved bundles)
   convert(bundle) turns Testsigma's steps into this tool's plain steps, one to
   one, keeping Testsigma's own wording where the tool has the same step:
       Navigate to …                      -> open …
       Tap on X                           -> click X
       Tap on X  +  Enter data V on focused element   -> type "V" into X
       Verify … displays an element X     -> verify element X is visible
       Verify … displays text T           -> verify text "T" on page
       While X is not visible + Swipe     -> scroll until element X visible, …
       If X is visible + Tap on Y         -> click if visible Y
       step group                         -> call <group>   (group imported too)
       Enter data V on focused element    -> type "V" into focused field
       Store V in X                       -> create variable X with value "V"
       Store text from the element E into a variable X -> store text of E as X
       Verify if A == / contains B        -> verify stored A equals / contains "${B}"
       … contains ignore-case with B      -> verify stored A contains "…" ignoring case
       Verify … element E has empty value -> store value of E + verify stored … equals ""
       Select option by value V in E      -> select option "V" in E
       Wait until the element E is not visible -> wait until element E is not visible
       Scroll the element E to N offset horizontally -> scroll element E horizontally by N
       Clear the text displayed in E      -> clear E
       Execute javascript J               -> run javascript "J"
       Block "Name"                       -> "# --- Name" heading, its steps inline
   Testsigma variable names lose their spaces (PRP City Filter -> PRP_City_Filter);
   whether a value is text or a ${variable} comes from Testsigma's own data type.
   Anything without an equivalent stays in the flow as a "# TODO (Testsigma): …"
   comment line — never silently dropped — and is listed in the preview.
   Steps that would send a REAL lead (submit / send enquiry / OTP / get quotes /
   request … / buy a sample / continue on a login-or-lead sheet) are written
   "# OFF:" so no run sends a lead until someone switches the step on.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT_DIR = os.path.join(BASE_DIR, "data", "testsigma_exports")

# ─────────────────────────────────────────────────────────────────────────────
# 1. PULL
# ─────────────────────────────────────────────────────────────────────────────
_status: dict[str, dict] = {}
_lock = threading.Lock()


def run_dir(run_id: str) -> str:
    return os.path.join(EXPORT_DIR, f"run_{int(run_id)}")


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name or "").strip("_") or "test_case"


def status(run_id: str) -> dict:
    with _lock:
        s = dict(_status.get(str(run_id)) or {})
    idx = os.path.join(run_dir(run_id), "index.json")
    if not s and os.path.exists(idx):
        with open(idx, encoding="utf-8") as f:
            s = {"state": "done", **json.load(f)}
    return s


def start_pull(run_id: str) -> dict:
    run_id = str(int(run_id))
    with _lock:
        cur = _status.get(run_id)
        if cur and cur.get("state") == "running":
            return cur
        _status[run_id] = {"state": "running", "done": 0, "total": 0, "message": "reading the run"}
    threading.Thread(target=_pull, args=(run_id,), daemon=True, name=f"ts-pull-{run_id}").start()
    return _status[run_id]


def _set(run_id: str, **kw) -> None:
    with _lock:
        _status.setdefault(run_id, {}).update(kw)


def _pull(run_id: str) -> None:
    from tools.testsigma_api import TestsigmaError, get
    try:
        results, page = [], 0
        while True:
            d = get("/api/v1/test_case_results", {"query": f"executionResultId:{run_id}", "page": page, "size": 50})
            results += d.get("content") or []
            if d.get("last", True) or page > 40:
                break
            page += 1
        # Top-level test cases only (step groups and loop iterations are children).
        cases = [r for r in results if not r.get("isStepGroup") and not r.get("parentId")]
        seen, ordered = set(), []
        for r in sorted(cases, key=lambda r: r.get("id") or 0):
            if r["testCaseId"] in seen:
                continue
            seen.add(r["testCaseId"])
            ordered.append(r)
        _set(run_id, total=len(ordered), message=f"{len(ordered)} test cases")
        out = run_dir(run_id)
        os.makedirs(out, exist_ok=True)
        groups: dict[int, dict] = {}
        elements: dict[int, dict] = {}
        index = []
        for n, r in enumerate(ordered, 1):
            tc_id = r["testCaseId"]
            name = (r.get("testCaseDetails") or {}).get("name") or (r.get("testCase") or {}).get("name") or str(tc_id)
            _set(run_id, done=n - 1, message=f"reading {name}")
            steps = get(f"/api/v2/test_steps/find_all/{tc_id}")
            used_groups = _collect_groups(steps, groups, get)
            _collect_elements(steps, elements, get)
            for gid in used_groups:
                _collect_elements(groups[gid]["steps"], elements, get)
            bundle = {"run_id": int(run_id), "test_case_id": tc_id, "name": name,
                      "result": r.get("result"), "message": r.get("message"),
                      "steps": steps,
                      "groups": {str(g): groups[g] for g in used_groups},
                      "elements": {str(e["id"]): e for e in elements.values()}}
            fname = f"{slug(name)}__{tc_id}.json"
            with open(os.path.join(out, fname), "w", encoding="utf-8") as f:
                json.dump(bundle, f, indent=1, ensure_ascii=False)
            index.append({"test_case_id": tc_id, "name": name, "file": fname,
                          "result": r.get("result"), "message": (r.get("message") or "")[:300],
                          "steps": len(steps)})
        with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as f:
            json.dump({"run_id": int(run_id), "pulled_at": time.time(), "cases": index}, f, indent=1)
        _set(run_id, state="done", done=len(ordered), message="pulled")
    except TestsigmaError as e:
        _set(run_id, state="error", message=str(e))
    except Exception as e:  # noqa: BLE001
        _set(run_id, state="error", message=f"{type(e).__name__}: {e}"[:400])


def _collect_groups(steps: list, groups: dict, get, _seen: set | None = None) -> list[int]:
    """Every step group these steps call, recursively (each fetched once, but its
    inner groups are listed for every case that uses it)."""
    seen = set() if _seen is None else _seen
    used: list[int] = []
    for s in steps or []:
        gid = s.get("stepGroupId")
        if s.get("type") == "STEP_GROUP" and gid and gid not in seen:
            seen.add(gid)
            if gid not in groups:
                groups[gid] = {"id": gid, "name": s.get("action", str(gid)), "steps": []}
                groups[gid]["steps"] = get(f"/api/v2/test_steps/find_all/{gid}")
            for inner in _collect_groups(groups[gid]["steps"], groups, get, seen):
                if inner not in used:
                    used.append(inner)
            if gid not in used:
                used.append(gid)
    return used


def _collect_elements(steps: list, elements: dict, get) -> None:
    for s in steps or []:
        for el in s.get("testStepElements") or []:
            eid = el.get("elementId")
            if eid and eid not in elements:
                try:
                    elements[eid] = get(f"/api/v2/elements/{eid}")
                except Exception:  # noqa: BLE001 — a missing element is reported at convert time
                    elements[eid] = {"id": eid, "name": el.get("name"), "definition": None}


def cases(run_id: str) -> list[dict]:
    idx = os.path.join(run_dir(run_id), "index.json")
    if not os.path.exists(idx):
        return []
    with open(idx, encoding="utf-8") as f:
        # "<id>_<hash>_aftertest" / "_beforetest" are Testsigma's own run hooks, not test cases.
        return [c for c in json.load(f)["cases"]
                if not re.search(r"_(after|before)test$", str(c.get("name") or ""), re.I)]


def _group_pool(run_id: str) -> dict:
    """Every step group saved anywhere in this run (older pulls left a nested group
    out of a case when an earlier case had already fetched its parent)."""
    pool: dict = {}
    d = run_dir(run_id)
    for fn in os.listdir(d):
        if fn.endswith(".json") and fn != "index.json":
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                pool.update(json.load(f).get("groups") or {})
    return pool


def load_bundle(run_id: str, test_case_id: int) -> dict:
    for c in cases(run_id):
        if int(c["test_case_id"]) == int(test_case_id):
            with open(os.path.join(run_dir(run_id), c["file"]), encoding="utf-8") as f:
                b = json.load(f)
            pool = _group_pool(run_id)
            groups = b.setdefault("groups", {})
            todo = [g for g in groups.values()]
            while todo:
                g = todo.pop()
                for st in g.get("steps") or []:
                    gid = str(st.get("stepGroupId") or "")
                    if st.get("type") == "STEP_GROUP" and gid and gid not in groups and gid in pool:
                        groups[gid] = pool[gid]
                        todo.append(pool[gid])
            for st in b.get("steps") or []:
                gid = str(st.get("stepGroupId") or "")
                if st.get("type") == "STEP_GROUP" and gid and gid not in groups and gid in pool:
                    groups[gid] = pool[gid]
            return b
    raise KeyError(f"test case {test_case_id} is not in run {run_id} — pull the run first")


# ─────────────────────────────────────────────────────────────────────────────
# 2. CONVERT
# ─────────────────────────────────────────────────────────────────────────────
#: Things that send a real lead / enquiry to clients when clicked or entered.
_LEAD = re.compile(
    r"send\s*enquiry|send\s*inquiry|get\s*best\s*price.*(submit|send|continue)|get\s*quotes?|"
    r"request\s*(catalogue|catalog|callback|call\s*back|quote|more\s*photos|sample)|buy\s*a?\s*sample|"
    r"get\s*best\s*deal|post\s*(your\s*)?requirement|place\s*order|book\s*now|"
    r"(lead|enquiry|rfq|gbp|price).*(submit|continue|proceed)|"
    r"(submit|continue|proceed).*(lead|enquiry|rfq)",
    re.I)
#: A button that STARTS a lead (the sheet it opens asks for mobile + OTP, and
#: signing in on that sheet is what sends the lead to the seller).
_LEAD_CTA = re.compile(
    r"ask\s*for\s*price|get\s*best\s*(price|deal)|request\s*(catalogue|catalog|callback|call\s*back|quote|sample)|"
    r"view\s*catalogue|send\s*enquiry|get\s*quotes?|buy\s*a?\s*sample|\brfq\b|post\s*(your\s*)?requirement",
    re.I)
#: Sign-in steps: mobile, continue, OTP, login / submit on the sign-in sheet.
_SIGNIN = re.compile(r"continue|submit|proceed|verify|(log\s*in|login|sign\s*in|signin).*(button|btn|cta)", re.I)
#: Closing or skipping a sheet never sends anything.
_DISMISS = re.compile(r"close|cross|cancel|skip|no\s*thanks|may\s*be\s*later|maybe\s*later|back", re.I)
#: Signing in (mobile + OTP) creates no lead. It runs, but only with these
#: numbers, which the site blocks from reaching clients (28 Sep, Prashant).
#: TEST_MOBILES in .env replaces the list. A step typing any other mobile is OFF.
TEST_MOBILES = {n.strip() for n in os.environ.get(
    "TEST_MOBILES", "9987996046,7977184984,7738176962,7738138167").split(",") if n.strip()}
_MOBILE = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")
#: After a lead is sent, the thank-you checks cannot pass with the send switched off.
_LEAD_AFTER = re.compile(r"acknowledg|thank\s*you|success(fully)?\s*(sent|submitted)|dear\s+\w+|toast", re.I)
_SCROLLABLE = re.compile(r"\s+and\s+with\s+scrollable\s+(true|false)\s*$", re.I)


def _load_aliases() -> dict:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "testsigma_aliases.json"),
                  encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {k: v for k, v in d.items() if not k.startswith("_")}
    out.update({k.lower(): v for k, v in out.items()})
    return out


_ALIASES = _load_aliases()


def _q(s: str) -> str:
    return '"' + str(s).replace('"', "'") + '"'


class _Ctx:
    """Elements used by one test case, named for this tool, with Testsigma's locator."""

    def __init__(self, bundle: dict):
        from locators.manager import normalise_name
        self._norm = normalise_name
        self.by_name: dict[str, dict] = {}
        for e in (bundle.get("elements") or {}).values():
            if e and e.get("name"):
                self.by_name[" ".join(str(e["name"]).lower().split())] = e
        self.used: dict[str, dict] = {}          # tool name -> {testsigma, xpath, type}
        self.cur: dict = {}                       # the Testsigma step being converted
        self.native = False                       # between "switch to native" and back
        self.optional_names: set[str] = set()     # elements from Android dialogs
        self.after_dialog = False                 # the step before tapped an Android dialog
        self.vars: set[str] = set()               # variables this case stores
        self.nums: dict[str, str] = {}            # variables stored as a plain number
        self.missing: list[str] = []
        self.skipped: list[str] = []

    def skip(self, why: str) -> list[str]:
        """A Testsigma step that has no meaning in a browser run — nothing to run."""
        self.skipped.append(why)
        return []

    def go_native(self, on: bool) -> list[str]:
        """Testsigma leaves the page for Android's own dialogs (Chrome's location
        permission). A browser run has no such dialog, so those taps are skipped."""
        self.native = on
        return self.skip("switch to Chrome's native dialogs" if on else "back to the page")

    def data(self, key: str) -> tuple[str | None, str | None]:
        """(type, value) of the current step's test data — type is raw / runtime / parameter …"""
        for d in self.cur.get("stepDataList") or []:
            if d.get("key") == key:
                return d.get("type"), d.get("value")
        return None, None

    def var(self, name: str, *, store: bool = False) -> str:
        """Testsigma variable names may have spaces; here they are one word."""
        v = re.sub(r"[^A-Za-z0-9_]+", "_", str(name).strip()).strip("_") or "value"
        if store:
            self.vars.add(v)
        return v

    def is_var(self, name: str) -> bool:
        """Is this a variable (not literal text)? Known from this case, or named like one."""
        n = str(name).strip()
        return self.var(n) in self.vars or ("_" in n and " " not in n)

    def value(self, key: str, shown: str) -> str:
        """A step's value as plain text or ${variable}, from Testsigma's own data type."""
        typ, val = self.data(key)
        val = shown if val is None else val
        if typ == "runtime" or (typ is None and self.is_var(val)):
            return "${" + self.var(val) + "}"
        if typ == "parameter":
            return "${" + self.var(val) + "}"
        return str(val)

    def text_el(self, text: str) -> str:
        """An element for 'the element with text T' — visible under Elements, not hidden in a step."""
        name = self._norm(f"{text} text", kind="locator")
        if name in ("text", "") or not re.search(r"[a-z0-9]", name.replace("text", "")):
            # Non-Latin text (e.g. Hindi) normalises to nothing — keep it unique and stable.
            import hashlib
            name = "text_" + hashlib.md5(str(text).encode("utf-8")).hexdigest()[:6]
        if name not in self.used:
            t = str(text).replace("'", "")
            self.used[name] = {"testsigma": f"element with text {text}",
                               "xpath": f"//*[normalize-space()='{t}']", "found": True, "native": False}
        return name

    def loc(self, ts_name: str) -> str:
        ts_name = _SCROLLABLE.sub("", ts_name.strip())
        alias = _ALIASES.get(ts_name) or _ALIASES.get(ts_name.lower())
        if alias:
            return alias
        name = self._norm(ts_name, kind="locator")
        if name in self.used:
            return name
        e = self.by_name.get(" ".join(ts_name.lower().split()))
        els = self.cur.get("testStepElements") or []
        if e is None and len(els) == 1:
            # The step text can shorten the element name ("units Dropdown" for
            # "units Dropdown1"); the step's own element reference is exact.
            real = " ".join(str(els[0].get("name") or "").lower().split())
            if real in self.by_name:
                ts_name, e = str(els[0]["name"]).strip(), self.by_name[real]
                name = self._norm(ts_name, kind="locator")
                if name in self.used:
                    return name
        definition = (e or {}).get("definition")
        kind = ((e or {}).get("locatorType") or "xpath").lower()
        if definition and kind != "xpath":
            d = str(definition)
            jsq = re.match(r"""^document\.querySelector\((['"])(.*)\1\)\s*;?$""", d.strip())
            definition = {"id": f"//*[@id='{d}']", "id_value": f"//*[@id='{d}']",
                          "name": f"//*[@name='{d}']", "name_value": f"//*[@name='{d}']",
                          "class_name": f"//*[contains(@class,'{d}')]",
                          "css_selector": d, "csspath": d,
                          "js_path": jsq.group(2) if jsq else d,
                          "link_text": f"//a[normalize-space()='{d}']",
                          "partial_link_text": f"//a[contains(normalize-space(),'{d}')]",
                          "tag_name": f"//{d}"}.get(kind, d)
        android_attr = bool(definition) and re.search(r"@(?:text|resource-id|content-desc)\b", str(definition))
        if android_attr:
            # An Android-app locator (@text='Consent'): the element is a phone dialog,
            # not page content. Keep a web form of it; the tap becomes optional.
            definition = re.sub(r"@text\s*=", "normalize-space()=", str(definition))
            self.optional_names.add(name)
        if not definition:
            self.missing.append(ts_name)
            t = ts_name.replace("'", "")
            definition = f"//*[contains(normalize-space(),'{t}')]"
        self.used[name] = {"testsigma": ts_name, "xpath": definition, "found": bool(e and e.get("definition")),
                           "native": str(definition).startswith(("//android.", "//XCUIElement"))}
        return name


# Testsigma step text -> this tool's step(s).  Checked in order; first match wins.
_MAP = [
    (r"^navigate to\s+(?P<url>\S+)$", lambda m, c: [f"open {m['url']}"]),
    (r"^delete all (?:local storage )?cookies.*$", lambda m, c: ["delete all cookies"]),
    (r"^wait for\s+(?P<n>\d+)\s*seconds?$", lambda m, c: [f"wait {m['n']} seconds"]),
    (r"^(?:tap on the back in the browser|navigate back|go back)$", lambda m, c: ["press back"]),
    (r"^(?:(?:refresh|reload) the (?:current )?page|tap on the refresh in the browser)$", lambda m, c: ["refresh page"]),
    (r"^(?:tap on|press) the (?P<k>space|enter|tab|escape|backspace)(?: key)?$",
     lambda m, c: [f"press key {m['k'].capitalize()}"]),
    (r"^switch to native view context$", lambda m, c: c.go_native(True)),
    (r"^switch to (?:context with name .+|web ?view.*)$", lambda m, c: c.go_native(False)),
    (r"^hide the keyboard$", lambda m, c: c.skip("no on-screen keyboard in the browser")),
    (r"^wait until the current page is loaded completely$", lambda m, c: ["wait for page to load"]),
    (r"^store (?:the )?text from the element\s+(?P<el>.+?)\s+into a variable\s+(?P<v>.+)$",
     lambda m, c: [f"store text of {c.loc(m['el'])} as {c.var(m['v'], store=True)}"]),
    (r"^store the count of elements identified by locator\s+(?P<el>.+?)\s+into a variable\s+(?P<v>.+)$",
     lambda m, c: [f"store count of {c.loc(m['el'])} as {c.var(m['v'], store=True)}"]),
    (r"^store the value for the attribute\s+(?P<a>\S+)\s+of the element\s+(?P<el>.+?)\s+into a variable\s+(?P<v>.+)$",
     lambda m, c: [f"store attribute {m['a']} of {c.loc(m['el'])} as {c.var(m['v'], store=True)}"]),
    (r"^store\s+(?P<val>.+?)\s+in\s+(?P<v>\S+)$", lambda m, c: _store_literal(m, c)),
    (r"^remove special char\s+(?P<ch>.+?)\s+from\s+(?P<src>.+?)\s+and store it in runtime\s+(?P<v>.+)$",
     lambda m, c: [f'remove "{m["ch"]}" from "${{{c.var(m["src"])}}}" and store as {c.var(m["v"], store=True)}']),
    (r"^verify if\s+(?P<a>.+?)\s+contains ignore-case with\s+(?P<b>.+)$",
     lambda m, c: [f'verify stored {c.var(m["a"])} contains '
                   f'"{("${" + c.var(m["b"]) + "}") if c.is_var(m["b"]) else m["b"]}" ignoring case']),
    (r"^verify if\s+(?P<a>.+?)\s+(?P<op>==|!=|contains)\s+(?P<b>.+)$", lambda m, c: _verify_if(m, c)),
    (r"^verify that the element\s+(?P<el>.+?)\s+has empty value$",
     lambda m, c: [f"store value of {c.loc(m['el'])} as ts_field_value",
                   'verify stored ts_field_value equals ""']),
    (r"^verify that the element\s+(?P<el>.+?)\s+has non-empty (?:text|value)$",
     lambda m, c: [f"store text of {c.loc(m['el'])} as ts_element_text",
                   'verify stored ts_element_text is not ""']),
    (r"^verify that the element\s+(?P<el>.+?)\s+has value\s+(?P<v>.+?)\s+for value$",
     lambda m, c: [f"store value of {c.loc(m['el'])} as ts_field_value",
                   f'verify stored ts_field_value equals "{c.value("testData", m["v"])}"']),
    (r"^verify that the element\s+(?P<el>.+?)\s+contains value\s+(?P<v>.+?)\s+for\s+(?P<a>\S+)$",
     lambda m, c: [f"store attribute {m['a']} of {c.loc(m['el'])} as ts_attribute",
                   f'verify stored ts_attribute contains "{c.value("testData", m["v"])}"']),
    (r"^verify that the element\s+(?P<el>.+?)\s+displays value\s+(?P<v>.+)$",
     lambda m, c: [f"verify element {c.loc(m['el'])} contains {_q(m['v'])}"]),
    (r"^verify that the element\s+(?P<el>.+?)\s+not displayed$",
     lambda m, c: [f"verify element {c.loc(m['el'])} is not visible"]),
    (r"^wait until the element\s+(?P<el>.+?)\s+is not (?:visible|displayed|present)$",
     lambda m, c: [f"wait until element {c.loc(m['el'])} is not visible"]),
    (r"^select option by (?:value|visible text|text|label)\s+(?P<v>.+?)\s+in the\s+(?P<el>.+?)(?:\s+list)?$",
     lambda m, c: [f'select option "{c.value("testData", m["v"])}" in {c.loc(m["el"])}']),
    (r"^execute javascript\s+(?P<js>.+)$", lambda m, c: [f'run javascript "{m["js"]}"']),
    (r"^clear the text displayed in the\s+(?P<el>.+?)\s+field$",
     lambda m, c: [f"clear {c.loc(m['el'])}"]),
    (r"^swipe the element\s+(?P<el>.+?)\s+into view$", lambda m, c: [f"scroll to {c.loc(m['el'])}"]),
    (r"^swipe (?P<d>bottom to top|top to bottom) for duration \d+ seconds?$",
     lambda m, c: ["scroll down 600" if m["d"].startswith("bottom") else "scroll up 600"]),
    (r"^scroll the element\s+(?P<el>.+?)\s+to\s+(?P<n>-?\d+)\s+offset horizontally$",
     lambda m, c: [f"scroll element {c.loc(m['el'])} horizontally by {m['n']}"]),
    (r"^scroll the window to page down offset vertically$", lambda m, c: ["scroll down 600"]),
    (r"^scroll the window to\s+(?P<v>[A-Za-z_]\w*)\s+offset vertically$",
     lambda m, c: [f"scroll down {c.nums[m['v']]}"] if m["v"] in c.nums else None),
    (r"^swipe bottom to middle(?: in the screen)?$", lambda m, c: ["scroll down 300"]),
    (r"^swipe middle to top(?: in the screen)?$", lambda m, c: ["scroll down 300"]),
    (r"^swipe top to middle(?: in the screen)?$", lambda m, c: ["scroll up 300"]),
    (r"^swipe middle to bottom(?: in the screen)?$", lambda m, c: ["scroll up 300"]),
    (r"^wait until the element\s+(?P<el>.+?)\s+is\s+(?:clickable|visible|present|enabled|displayed)$",
     lambda m, c: [f"wait until element {c.loc(m['el'])} is visible"]),
    (r"^wait until the text\s+(?P<t>.+?)\s+is (?:present|visible|displayed)(?: on the current page)?$",
     lambda m, c: [f"verify text {_q(m['t'])} on page"]),
    (r"^verify that the current page displays text\s+(?P<t>.+)$",
     lambda m, c: [f"verify text {_q(m['t'])} on page"]),
    (r"^verify that the current page does not display (?:the )?text\s+(?P<t>.+)$",
     lambda m, c: [f"verify page does not contain {_q(m['t'])}"]),
    (r"^verify that the current page displays an element\s+(?P<el>.+)$",
     lambda m, c: [f"verify element {c.loc(m['el'])} is visible"]),
    (r"^verify that the current page does not display (?:an )?element\s+(?P<el>.+)$",
     lambda m, c: [f"verify element {c.loc(m['el'])} is not visible"]),
    (r"^verify that the element\s+(?P<el>.+?)\s+displays text contains\s+(?P<t>.+)$",
     lambda m, c: [f'verify element {c.loc(m["el"])} contains "{c.value("testData", m["t"])}"']),
    (r"^verify that the element\s+(?P<el>.+?)\s+(?:displays|contains) text\s+(?P<t>.+)$",
     lambda m, c: [f"verify element {c.loc(m['el'])} contains {_q(m['t'])}"]),
    (r"^verify that the element\s+(?P<el>.+?)\s+is (?:displayed|visible|present)$",
     lambda m, c: [f"verify element {c.loc(m['el'])} is visible"]),
    (r"^verify that the element\s+(?P<el>.+?)\s+is not (?:displayed|visible|present)$",
     lambda m, c: [f"verify element {c.loc(m['el'])} is not visible"]),
    (r"^verify that the current page url contains\s+(?P<t>.+)$",
     lambda m, c: ["store page url as current_url", f"verify stored current_url contains {_q(m['t'])}"]),
    (r"^verify that the current page title (?:is|contains)\s+(?P<t>.+)$",
     lambda m, c: [f"verify page title contains {_q(m['t'])}"]),
    (r"^enter\s+(?:data\s+)?(?P<v>.+?)\s+in(?:to)? the\s+(?P<el>.+?)(?:\s+field)?$",
     lambda m, c: [f"type {_q(m['v'])} into {c.loc(m['el'])}"]),
    (r"^click on the element\s+(?P<el>.+?)\s+using javascript executor$",
     lambda m, c: [f"js click {c.loc(m['el'])}"]),
    (r"^tap on the element with text\s+(?P<t>.+?)\s+if (?:visible|present|displayed)$",
     lambda m, c: [f"click if visible {c.text_el(m['t'])}"]),
    (r"^tap on the element with text\s+(?P<t>.+?)$",
     lambda m, c: [f"click if visible {c.text_el(m['t'])}" if c.after_dialog else f"click {c.text_el(m['t'])}"]),
    (r"^tap on\s+(?P<el>.+?)\s+if (?:visible|present|displayed)$",
     lambda m, c: [f"click if visible {c.loc(m['el'])}"]),
    (r"^(?:tap|click) on\s+(?P<el>.+?)$", lambda m, c: _tap(m, c)),
    (r"^scroll the window to\s+(?P<n>-?\d+)\s+offset vertically$",
     lambda m, c: [f"scroll down {m['n']}"]),
    (r"^scroll\s+(?:down\s+|up\s+)?to the element\s+(?P<el>.+?)\s+into view$",
     lambda m, c: [f"scroll to {c.loc(m['el'])}"]),
    (r"^swipe bottom to top(?: in the screen)?$", lambda m, c: ["scroll down 600"]),
    (r"^swipe top to bottom(?: in the screen)?$", lambda m, c: ["scroll up 600"]),
]


def _tap(m, c: "_Ctx"):
    """Tap on X. A tap on an Android dialog element (Chrome's consent sheet) is
    optional in a browser run — that sheet only exists on the phone."""
    name = c.loc(m["el"])
    return [f"click if visible {name}" if name in c.optional_names else f"click {name}"]


def _store_literal(m, c: "_Ctx"):
    """Testsigma "Store <value> in <variable>"."""
    val = m["val"].strip()
    if val.lower().startswith("numberfunctions"):
        return None                                  # a counter inside a loop — no plain equivalent
    typ, raw = c.data("testData1")
    v = c.var(m["v"], store=True)
    if typ == "runtime":
        val = "${" + c.var(raw) + "}"
    if re.fullmatch(r"-?\d+", val):
        c.nums[v] = val
    return [f'create variable {v} with value "{val}"']


def _verify_if(m, c: "_Ctx"):
    """Testsigma "Verify if A == / != / contains B" on stored values or text."""
    ta, va = c.data("testData1")
    tb, vb = c.data("testData3")
    a, b = (va or m["a"]).strip(), (vb or m["b"]).strip()
    a_var = ta == "runtime" if ta else c.is_var(a)
    b_var = tb == "runtime" if tb else c.is_var(b)
    op = {"==": "equals", "!=": "is not", "contains": "contains"}[m["op"]]
    if not a_var and b_var and op != "contains":
        a, b, a_var, b_var = b, a, b_var, a_var      # "Text == variable" reads the other way round here
    if not a_var:
        return None
    rhs = "${" + c.var(b) + "}" if b_var else b.replace('"', "'")
    return [f'verify stored {c.var(a)} {op} "{rhs}"']


_COMPILED = [(re.compile(p, re.I), h) for p, h in _MAP]
_JS_TAP = re.compile(r"^click on the element\s+(?P<el>.+?)\s+using javascript executor$", re.I)
_TAP = re.compile(r"^(?:tap|click) on\s+(?P<el>.+?)(?:\s+if (?:visible|present|displayed))?$", re.I)
_ENTER_FOCUSED = re.compile(r"^enter data\s+(?P<v>.+?)\s+on (?:the )?focused element$", re.I)
_WHILE = re.compile(r"^while (?:the )?element\s+(?P<el>.+?)\s+is not (?:visible|displayed|present)(?: on the page)?$", re.I)
_IF_VISIBLE = re.compile(r"^(?:if )?(?:the )?element\s+(?P<el>.+?)\s+is (?:visible|displayed|present)(?: on the page)?$", re.I)


def _one(action: str, ctx: _Ctx) -> list[str] | None:
    # "…and With Scrollable TRUE/FALSE" is a Testsigma mobile option, not part of the check.
    raw = " ".join(str(action or "").split())
    scroll_first = bool(re.search(r"with\s+scrollable\s+true\s*$", raw, re.I))
    a = _SCROLLABLE.sub("", raw)
    if ctx.native and not re.match(r"^switch to", a, re.I):
        # A tap on an Android dialog (e.g. "Allow" location for the Chrome APP).
        # That is the phone's permission for Chrome, not a grant to the site, and
        # a browser run has no such dialog — so nothing to run. (Granting the site
        # location instead changes what justdial shows: NCT pages then redirect.)
        return ctx.skip(f"Android dialog: {a}")
    for rx, h in _COMPILED:
        m = rx.match(a)
        if m:
            out = h(m, ctx)
            if scroll_first and out:
                # "…With Scrollable TRUE": Testsigma scrolls the element into view
                # before checking it — pages that load or pop up on scroll need that.
                t = re.match(r"^(?:verify element|store text of|store value of|store attribute \S+ of) (\S+)(?=\s)",
                             out[0])
                if t and not out[0].endswith("is not visible"):
                    out = [f"scroll to {t.group(1)}"] + out
            # A dialog tap makes the next "tap on the element with text …" optional too.
            ctx.after_dialog = bool(out) and any(
                ln.startswith("click if visible ") and ln.split()[-1] in ctx.optional_names for ln in out)
            return out
    return None


def _last_click(out: list[str]):
    """The click that focused a field: the last click within the previous few lines
    (Testsigma often waits between the tap and "Enter data … on focused element")."""
    for k in range(len(out) - 1, max(-1, len(out) - 5), -1):
        m = re.match(r"^(# OFF: )?(?:js )?click(?: if visible)? (\S+)$", out[k])
        if m:
            return k, m.group(1) or "", m.group(2)
        if not re.match(r"^(?:# OFF: )?(?:wait|create variable)", out[k]):
            return None
    return None


def _normalise_steps(steps: list) -> list[dict]:
    """Testsigma's flat list -> top-level steps with their loop / if children."""
    by_parent: dict = {}
    for s in steps or []:
        by_parent.setdefault(s.get("parentId"), []).append(s)
    # Keep Testsigma's own order: find_all returns steps in execution order
    # (position / stepOrder are not filled in, and ids follow creation time).

    def build(parent):
        out = []
        for s in by_parent.get(parent, []):
            out.append({**s, "_children": build(s.get("id"))})
        return out
    top = build(None)
    return top


def convert(bundle: dict, *, platform: str = "mobilesite") -> dict:
    """Bundle -> {"flow": text, "groups": {name: [lines]}, "elements": {...}, "todo": [...], "off": [...]}"""
    ctx = _Ctx(bundle)
    groups_out: dict[str, list[str]] = {}
    todo: list[str] = []
    off: list[str] = []
    group_src = {int(k): v for k, v in (bundle.get("groups") or {}).items()}

    lead_groups: set[str] = set()
    signin_groups: set[str] = set()     # groups that sign in (continue / submit / verify)

    def lines_for(steps: list, where: str) -> list[str]:
        st = {"lead_sent": False}
        lines = emit(_normalise_steps(steps), where, st)
        gslug = slug(where.replace("step group ", "", 1))[:50].rstrip("_")
        if st.get("had_lead"):
            lead_groups.add(gslug)
        if st.get("signs_in"):
            signin_groups.add(gslug)
        return lines

    def todo_tree(node: dict, where: str, out: list[str], depth: int = 0) -> None:
        """An unconverted step and everything inside it, written as TODO comments — nothing hidden."""
        for ch in node.get("_children") or []:
            a = " ".join(str(ch.get("action") or "").split()) or ch.get("conditionType") or "step"
            todo.append(f"{where}: (inside) {a}")
            out.append(f"# TODO (inside the step above){'  ' * depth}: {a}")
            todo_tree(ch, where, out, depth + 1)

    def emit(flat: list, where: str, st: dict) -> list[str]:
        out: list[str] = []
        i = 0
        while i < len(flat):
            s = flat[i]
            ctx.cur = s
            action = " ".join(str(s.get("action") or "").split())
            disabled = bool(s.get("disabled"))
            emitted: list[str] | None
            consumed = 1
            if s.get("type") == "BLOCK":
                # A named block in Testsigma is only a heading over its steps.
                out.append(f"# --- {action or 'Block'}")
                inner = emit(s.get("_children") or [], where, st)
                out.extend(f"# OFF: {ln}" if disabled and ln and not ln.startswith("#") else ln for ln in inner)
                i += 1
                continue
            if s.get("type") == "STEP_GROUP":
                # Step group names here are at most 50 characters.
                gname = (slug(action) if action else f"group_{s.get('stepGroupId')}")[:50].rstrip("_")
                g = group_src.get(s.get("stepGroupId"))
                if g is not None and gname not in groups_out:
                    groups_out[gname] = []          # placeholder: recursion guard
                    groups_out[gname] = lines_for(g.get("steps") or [], f"step group {action}")
                    ctx.cur = s
                emitted = [f"call {gname}"]
                if st.get("lead_cta") and gname in signin_groups:
                    # A sign-in group called right after a lead button: signing in
                    # there is what sends the lead.
                    st["call_is_lead"] = True
                if gname in lead_groups:
                    # The group sends (a switched-off) lead: what follows the call
                    # in this test depends on it, exactly as if the steps were inline.
                    st["after_group_lead"] = True
            elif _WHILE.match(action):
                el = _WHILE.match(action)["el"]
                body = [" ".join(str(c.get("action") or "").split()).lower() for c in s["_children"]]
                scrolling = [b.startswith(("swipe", "scroll", "wait", "pagescroll")) or
                             b.startswith("store numberfunctions") for b in body]
                # Taps inside a scroll loop close whatever pops up on the way (a
                # sign-in sheet, the GVS popup): run them as "click if visible".
                closers = [c for c, b, sc in zip(s["_children"], body, scrolling)
                           if not sc and (_TAP.match(b) or _JS_TAP.match(b))]
                if all(sc or c in closers for c, sc in zip(s["_children"], scrolling)):
                    ctx.cur = s
                    target = ctx.loc(el)
                    emitted = []
                    for c in closers:          # a popup Testsigma closed while scrolling
                        ctx.cur = c
                        ca = " ".join(str(c.get("action")).split())
                        m_ = _JS_TAP.match(ca) or _TAP.match(ca)
                        emitted.append(f"click if visible {ctx.loc(m_['el'])}")
                    emitted.append(f"scroll until element {target} visible, scroll by 600 pixels, "
                                   f"scroll count 15, scroll wait 1")
                else:
                    emitted = None
            elif _IF_VISIBLE.match(action) and s["_children"] and all(
                    _TAP.match(" ".join(str(c.get("action") or "").split())) for c in s["_children"]):
                emitted = []
                for c in s["_children"]:
                    ctx.cur = c
                    emitted.append(f"click if visible {ctx.loc(_TAP.match(' '.join(str(c.get('action')).split()))['el'])}")
            elif (_TAP.match(action) and i + 1 < len(flat)
                  and _ENTER_FOCUSED.match(" ".join(str(flat[i + 1].get("action") or "").split()))):
                v = _ENTER_FOCUSED.match(" ".join(str(flat[i + 1].get("action")).split()))["v"]
                ctx.cur = flat[i + 1]
                v = ctx.value("testData", v)
                ctx.cur = s
                emitted = [f"type {_q(v)} into {ctx.loc(_TAP.match(action)['el'])}"]
                disabled = disabled or bool(flat[i + 1].get("disabled"))
                consumed = 2
            elif _ENTER_FOCUSED.match(action):
                # "Tap on X" (maybe with waits between) + "Enter data V on focused element"
                # becomes one "type" step; otherwise keys go to whatever has focus, as in Testsigma.
                v = ctx.value("testData", _ENTER_FOCUSED.match(action)["v"])
                lc = _last_click(out)
                if lc and not lc[1] and not disabled:
                    out[lc[0]] = f"type {_q(v)} into {lc[2]}"
                    i += 1
                    continue
                emitted = [f"type {_q(v)} into focused field"]
                if lc and lc[1] and st["lead_sent"]:
                    disabled = True        # the field it types into belongs to the switched-off lead
            else:
                emitted = _one(action, ctx)
            if emitted is None:
                todo.append(f"{where}: {action}")
                out.append(f"# TODO (Testsigma, no equivalent yet): {action}")
                todo_tree(s, where, out)
                i += consumed
                continue
            for ln in emitted:
                if ln.startswith(("open ", "delete all cookies", "refresh page")):
                    st["lead_sent"] = False      # a fresh page: nothing below depends on the lead any more
                # A scroll loop only names the element it scrolls to ("While Request
                # Catalogue is not visible"); its lines never send anything.
                acts = not ln.startswith(("open ", "wait ", "verify ", "scroll", "call ", "store ", "create "))
                if ln.startswith(("open ", "delete all cookies", "refresh page")):
                    st["lead_cta"] = False
                # Signing in with a blocked test number sends nothing — unless the
                # sign-in sheet was opened by a lead button (Ask for Price, Get Best
                # Price, Request Catalogue…): then signing in IS the lead.
                is_lead = acts and not _WHILE.match(action) and (
                    bool(_LEAD.search(action)) or
                    (st.get("lead_cta") and bool(_SIGNIN.search(action)) and not _DISMISS.search(action)
                     and ln.startswith(("click", "js click"))))
                # The Open RFQ seller-type choice only filters results — no leadgen
                # call (confirmed by Prashant, 28 Sep).
                seller_type = bool(re.search(r"seller\s*type", action, re.I))
                if seller_type:
                    is_lead = False
                if st.pop("call_is_lead", False) and ln.startswith("call "):
                    is_lead = True
                if (acts and _SIGNIN.search(action) and not _DISMISS.search(action)
                        and ln.startswith(("click", "js click"))):
                    st["signs_in"] = True
                if acts and _LEAD_CTA.search(action) and not seller_type and ln.startswith(("click", "js click")):
                    st["lead_cta"] = True
                # After a switched-off lead, the steps that follow it (OTP, thank-you checks,
                # the next sheet) cannot run either — until the page is opened again.
                after_lead = st["lead_sent"] and not re.match(r"^wait \d", ln)
                if is_lead:
                    st["lead_sent"] = True
                    st["had_lead"] = True
                other_mobile = ln.startswith("type ") and any(
                    m_ not in TEST_MOBILES for m_ in _MOBILE.findall(ln.split(" into ")[0]))
                if disabled or is_lead or after_lead or other_mobile:
                    why = "disabled in Testsigma" if disabled else (
                        "REAL LEAD" if is_lead else
                        "mobile number is not one of the blocked test numbers" if other_mobile else
                        "only after the lead step above")
                    off.append(f"{where}: {ln}   ({why})")
                    out.append(f"# OFF: {ln}")
                else:
                    out.append(ln)
            if st.pop("after_group_lead", False):
                st["lead_sent"] = True
                st["had_lead"] = True
            i += consumed
        return out

    body = lines_for(bundle.get("steps") or [], "test case")

    # A group whose every step is OFF / TODO has nothing to run and cannot be
    # saved; the calls to it are switched off (repeat: a group may only call such groups).
    def runnable(lines):
        return [ln for ln in lines if ln and not ln.startswith("#")]
    changed = True
    while changed:
        changed = False
        empty = {g for g, ls in groups_out.items() if not runnable(ls)}
        for g, ls in list(groups_out.items()) + [("", body)]:
            for k, ln in enumerate(ls):
                if ln.startswith("call ") and ln[5:].strip() in empty:
                    ls[k] = f"# OFF: {ln}"
                    off.append(f"{g or 'test case'}: {ln}   (the group has no step that can run)")
                    changed = True
    name = bundle.get("name", "")
    header = [f"# Platform: {platform}",
              f"# Imported from Testsigma: {name} (test case {bundle.get('test_case_id')}, run {bundle.get('run_id')}, "
              f"last result {bundle.get('result')})",
              "# Tags: regression"]
    if off:
        header.append("# OFF steps: every step that would send a REAL lead / enquiry / OTP is switched off,")
        header.append("#   plus the checks that only pass after it. Switch them on only when a lead is wanted.")
    if todo:
        header.append("# TODO lines: Testsigma steps with no equivalent here yet — decide per line.")
    native = [n for n, e in ctx.used.items() if e["native"]]
    flow = "\n".join(header + [""] + body) + "\n"
    return {"name": name, "flow": flow, "groups": groups_out, "elements": ctx.used,
            "missing_elements": sorted(set(ctx.missing)), "todo": todo, "off": off, "native": native,
            "skipped": ctx.skipped,
            "steps": sum(1 for ln in body if ln and not ln.startswith("#"))}


# ─────────────────────────────────────────────────────────────────────────────
# 3. IMPORT (writes into the portal)
# ─────────────────────────────────────────────────────────────────────────────
_REGISTRY = os.path.join(EXPORT_DIR, "imported.json")


def _registry() -> dict:
    """What this importer created: only those may be replaced by a re-import.
    A hand-built test case or step group with the same name is never overwritten."""
    try:
        with open(_REGISTRY, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    d.setdefault("flows", [])
    d.setdefault("groups", [])
    return d


def _save_registry(d: dict) -> None:
    os.makedirs(EXPORT_DIR, exist_ok=True)
    tmp = _REGISTRY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    os.replace(tmp, _REGISTRY)


def import_case(run_id: str, test_case_id: int, *, platform: str = "mobilesite",
                overwrite: bool = False) -> dict:
    from core import reusable_steps
    from locators.manager import add_locator, load_locators, normalise_name

    bundle = load_bundle(run_id, test_case_id)
    res = convert(bundle, platform=platform)
    flow_name = slug(res["name"])
    flow_path = os.path.join(BASE_DIR, "flows", flow_name + ".flow")
    reg = _registry()
    if os.path.exists(flow_path):
        if not overwrite:
            return {"name": flow_name, "status": "exists", "detail": "a test case with this name already exists"}
        if flow_name not in reg["flows"]:
            return {"name": flow_name, "status": "exists",
                    "detail": "a hand-built test case has this name — it is never replaced by an import"}
    group = normalise_name("ts " + res["name"], kind="group")[:60]
    existing = load_locators()
    known = {n for els in existing.values() if isinstance(els, dict) for n in els}
    added = 0
    by_xpath = {x: n for els in existing.values() if isinstance(els, dict)
                for n, x in els.items() if isinstance(x, str)}
    renames: dict[str, str] = {}
    owner = {n: g for g, els in existing.items() if isinstance(els, dict) for n in els}
    fixed = 0
    for lname, meta in res["elements"].items():
        if meta["native"]:
            continue
        if lname in known:
            # A locator this importer saved earlier (group ts_…) follows the
            # converter's latest reading of Testsigma; hand-made ones are kept.
            g = owner.get(lname, "")
            if g.startswith("ts_") and existing[g].get(lname) != meta["xpath"] and meta["found"]:
                existing[g][lname] = meta["xpath"]
                fixed += 1
            continue
        if meta["xpath"] in by_xpath:
            # The same element is already saved under another name: use that one
            # (the locator store keeps one name per XPath).
            renames[lname] = by_xpath[meta["xpath"]]
            continue
        if add_locator(group, lname, meta["xpath"]):
            added += 1
            by_xpath[meta["xpath"]] = lname
    if fixed:
        from locators.manager import save_locators
        cur = load_locators()
        for g, els in existing.items():
            if g.startswith("ts_") and isinstance(els, dict):
                for n, x in els.items():
                    if n in cur.get(g, {}) and cur[g][n] != x:
                        cur[g][n] = x
        save_locators(cur)
    if renames:
        rx = re.compile(r"\b(" + "|".join(map(re.escape, renames)) + r")\b")
        fix = lambda ln: rx.sub(lambda m: renames[m.group(1)], ln)  # noqa: E731
        res["flow"] = "\n".join(fix(ln) for ln in res["flow"].split("\n"))
        res["groups"] = {g: [fix(ln) for ln in ls] for g, ls in res["groups"].items()}
    kept: list[str] = []
    for gname, lines in res["groups"].items():
        # A step group holds runnable steps only: comment lines inside a group
        # reach the parser. Its OFF / TODO lines are reported by the preview.
        steps = [ln for ln in lines if ln and not ln.startswith("#")]
        if not steps:
            continue
        mine = gname in reg["groups"]
        try:
            reusable_steps.save(gname, steps, overwrite=overwrite and mine, platform=platform)
            if gname not in reg["groups"]:
                reg["groups"].append(gname)
        except ValueError as e:
            if not str(e).startswith("DUPLICATE:"):
                raise
            kept.append(gname)                  # an existing group of the same name is used as it is
    os.makedirs(os.path.dirname(flow_path), exist_ok=True)
    with open(flow_path, "w", encoding="utf-8") as f:
        f.write(res["flow"])
    if flow_name not in reg["flows"]:
        reg["flows"].append(flow_name)
    _save_registry(reg)
    return {"name": flow_name, "status": "imported", "steps": res["steps"], "elements_added": added,
            "groups": list(res["groups"]), "existing_groups_kept": kept, "renamed_elements": renames,
            "todo": len(res["todo"]),
            "off": len(res["off"]), "missing_elements": res["missing_elements"]}
