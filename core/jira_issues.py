"""
core/jira_issues.py — failures from a run, turned into Jira tickets you raise in one click.

Flow
----
1. draft(run_id=… | plan_run=…) reads the saved run report(s) and proposes one
   issue per distinct failure. The same failing step on several devices is ONE
   issue listing every device. Problems with the test itself (unknown element,
   missing value, bad step) are listed apart as "fix the test" and never raised.
2. Each issue is marked confirmed when it reproduced — failed again on the
   retry, or on a second device — otherwise "needs confirmation".
3. The ticket type follows the team rule:
       live site   confirmed → Bug (fixed)      not confirmed → Bug or Concern
       any other   → Defect or Concern (confirmed defaults to Defect)
4. raise_issues() creates the tickets with the TESTER'S OWN Jira token (saved
   once, per portal user): project and label from the story, "Relates" link to
   the story, owner → Assignee, severity → Priority, the failure screenshots
   (and video) attached. Without a token, prefill_url() opens Jira's own Create
   form in their logged-in browser; csv_bytes() gives a file for bulk upload.

Reads (project, duplicate search, assignee search) use the server's JIRA_PAT;
tickets are only ever CREATED with the tester's own token, when they press
Raise. Nothing here assigns anyone on its own — the owner is what the tester
picked.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import threading
from urllib.parse import urlencode, urlparse

from config.settings import DATA_DIR, LOGS_DIR, SCREENSHOTS_DIR

LOGINS_FILE = os.path.join(DATA_DIR, "jira_logins.json")     # git-ignored
RAISED_FILE = os.path.join(DATA_DIR, "raised_issues.json")    # git-ignored
PLAN_RUNS_DIR = os.path.join(DATA_DIR, "plan_runs")
_lock = threading.Lock()

ISSUE_TYPES = ("Bug", "Defect", "Concern")
PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")
KEY_RX = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
MAX_ATTACH_BYTES = 20 * 1024 * 1024


class IssueError(ValueError):
    """Something a person can act on (no login, Jira refused, missing story)."""


# ─────────────────────────────────────────────────────────────────────────────
# Jira connections
# ─────────────────────────────────────────────────────────────────────────────
def base_url() -> str:
    return (os.getenv("JIRA_BASE_URL") or "https://jdjira.justdial.com").rstrip("/")


def _session(token: str):
    import requests
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    return s


def _server() :
    pat = (os.getenv("JIRA_PAT") or os.getenv("JIRA_TOKEN") or "").strip().strip("'\"")
    if not pat:
        raise IssueError("Jira reading is not configured on the server (JIRA_PAT in .env).")
    return _session(pat)


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(tmp, 0o600)          # tokens: owner-readable only
    except OSError:
        pass
    os.replace(tmp, path)


def save_login(username: str, email: str, token: str) -> dict:
    """Check the token against Jira (who is it?) and keep it for this portal user."""
    username = (username or "").strip().lower()
    token = (token or "").strip()
    if not username:
        raise IssueError("Sign in to the portal first.")
    if not token:
        raise IssueError("Paste your Jira Personal Access Token.")
    try:
        r = _session(token).get(f"{base_url()}/rest/api/2/myself", timeout=20)
    except Exception as e:  # noqa: BLE001
        raise IssueError(f"Could not reach Jira: {e}") from e
    if r.status_code in (401, 403):
        raise IssueError("Jira did not accept that token — create a new one and paste it again.")
    if r.status_code >= 400:
        raise IssueError(f"Jira answered {r.status_code} when checking the token.")
    me = r.json()
    jira_email = (me.get("emailAddress") or "").lower()
    if email and jira_email and email.strip().lower() != jira_email:
        raise IssueError(f"That token belongs to {jira_email}, not {email}.")
    with _lock:
        data = _read_json(LOGINS_FILE)
        prev = data.get(username, {})
        data[username] = {**prev, "email": jira_email or email.strip().lower(),
                          "token": token, "jira_user": me.get("name") or me.get("key") or "",
                          "display": me.get("displayName") or "",
                          "saved_at": _dt.datetime.now().isoformat(timespec="seconds")}
        _write_json(LOGINS_FILE, data)
    return login_status(username)


def login_status(username: str) -> dict:
    u = _read_json(LOGINS_FILE).get((username or "").lower()) or {}
    return {"connected": bool(u.get("token")), "email": u.get("email", ""),
            "display": u.get("display", ""), "jira_user": u.get("jira_user", ""),
            "defaults": u.get("defaults", {})}


def forget_login(username: str) -> None:
    with _lock:
        data = _read_json(LOGINS_FILE)
        u = data.get((username or "").lower())
        if u:
            u.pop("token", None)
            _write_json(LOGINS_FILE, data)


def save_defaults(username: str, defaults: dict) -> None:
    """Common inputs (owners, priority) remembered per tester — asked once."""
    keep = {k: v for k, v in (defaults or {}).items()
            if k in ("defect_owner", "concern_owner", "bug_owner", "priority") and isinstance(v, (str, dict))}
    with _lock:
        data = _read_json(LOGINS_FILE)
        data.setdefault((username or "").lower(), {})["defaults"] = keep
        _write_json(LOGINS_FILE, data)


def _user_token(username: str) -> str:
    return (_read_json(LOGINS_FILE).get((username or "").lower()) or {}).get("token", "")


# ─────────────────────────────────────────────────────────────────────────────
# Reading run reports
# ─────────────────────────────────────────────────────────────────────────────
def _report(path_or_name: str) -> dict:
    p = path_or_name if os.path.isabs(path_or_name) else os.path.join(LOGS_DIR, path_or_name)
    return _read_json(p)


def _flow_header(test_case: str) -> dict:
    """'# Story:' / '# Tags:' / '# Platform:' lines of a test case."""
    from config.settings import BASE_DIR
    out = {}
    try:
        with open(os.path.join(BASE_DIR, "flows", f"{test_case}.flow"), encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^#\s*(Story|Tags|Platform|Purpose)\s*:\s*(.*)$", line.strip(), re.I)
                if m:
                    out.setdefault(m.group(1).lower(), m.group(2).strip())
                elif line.strip() and not line.startswith("#"):
                    break
    except OSError:
        pass
    return out


def story_of(test_case: str, *names: str) -> str:
    h = _flow_header(test_case)
    for text in (h.get("story", ""), h.get("tags", ""), *names):
        m = KEY_RX.search(text or "")
        if m:
            return m.group(1)
    return ""


_AUTOMATION_RX = re.compile(
    r"not in (your|the) element list|is not defined in|could not be resolved|unknown command|"
    r"invalid syntax|not stored in memory|is not set at this point|no data set called|"
    r"circular call|did not understand|no handler|unknown element|engine|"
    r"has no matching|is never closed|step does not parse", re.I)
_SETUP_RX = re.compile(r"site did not load|net::err|name_not_resolved|connection refused|"
                       r"err_tunnel|browser has been closed|target closed", re.I)


def classify(reason: str, step: str) -> str:
    """'automation' (fix the test), 'setup' (site/browser/network) or 'product'."""
    text = f"{reason or ''}"
    if _AUTOMATION_RX.search(text) or step == "ENGINE":
        return "automation"
    if _SETUP_RX.search(text):
        return "setup"
    return "product"


def _norm_reason(reason: str) -> str:
    r = re.sub(r"\d+(\.\d+)?", "#", (reason or "").lower())
    r = re.sub(r"actual:.*", "", r, flags=re.S)
    return re.sub(r"\s+", " ", r).strip()[:160]


def _site(meta_env) -> dict:
    """{'name', 'host', 'live'} from a report's site_env (None = as written)."""
    if isinstance(meta_env, dict) and meta_env.get("host"):
        host = meta_env["host"].lower()
        return {"name": meta_env.get("name") or host, "host": host,
                "live": host in ("www.justdial.com", "justdial.com", "t.justdial.com")}
    return {"name": "", "host": "", "live": None}          # decided from the URLs


def _resolve_display(step: str, host: str = "") -> str:
    """${url} values filled in for a reader (secrets and mobile numbers never)."""
    try:
        from execution.test_data import get_all
        values = get_all("")
    except Exception:  # noqa: BLE001
        values = {}

    def sub(m):
        name = m.group(1)
        entry = values.get(name)
        v = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(entry, dict) and entry.get("is_secret"):
            return m.group(0)
        if not isinstance(v, str) or not v:
            return m.group(0)
        if re.fullmatch(r"[6-9]\d{9}", v):
            return v[:2] + "xxxxx" + v[-3:]
        if name.lower().startswith(("pass", "token", "secret")):
            return m.group(0)
        if host and v.startswith("http"):
            p = urlparse(v)
            if (p.hostname or "").endswith("justdial.com"):
                v = v.replace(p.netloc, host, 1)
        return v
    out = re.sub(r"\$\{([^}]+)\}", sub, step)
    if host:
        # A justdial address typed straight into the step moves to the run's site too.
        out = re.sub(r"https?://([a-z0-9-]+\.)?justdial\.com",
                     lambda m: f"https://{host}", out)
    return out


def _first_url(results: list[dict], host: str) -> str:
    for r in results:
        m = re.match(r"^open\s+(.+)$", (r.get("test_name") or "").strip(), re.I)
        if m:
            return _resolve_display(m.group(1).strip(), host)
    return ""


#: Every issue raised from the portal carries this label, so they can be found together.
DEFAULT_LABEL = "ps_codeless_automation"

_MODULE_LABEL = {"website": "Website", "mobilesite": "Mobile Site", "android": "Android App",
                 "ios": "iOS App", "hybrid": "Hybrid App"}
_ACRONYMS = {"pdp", "prp", "otp", "url", "nct", "rfq", "gvs", "jd", "b2b", "ui", "cta", "faq", "id"}


def _r(name: str) -> str:
    """'gallery_360_icon' → 'gallery 360° icon', 'pdp_first_photo' → 'PDP first photo'."""
    words = re.split(r"[_\s]+", (name or "").strip())
    out = [w.upper() if w.lower() in _ACRONYMS else ("360°" if w == "360" else w) for w in words if w]
    return " ".join(out)


def _module_of(test_case: str, rep: dict | None = None) -> str:
    m = ((rep or {}).get("meta") or {}).get("platform") or ""
    if not m:
        try:
            from api.routes.projects import _platform_of
            m = _platform_of(test_case)
        except Exception:  # noqa: BLE001
            m = ""
    return m


def _tap(module: str) -> str:
    return "Tap" if module in ("mobilesite", "android", "ios", "hybrid") else "Click"


def step_plain(step: str, module: str = "") -> str:
    """A test step as a person would write it in a bug report."""
    s = (step or "").strip()
    tap = _tap(module)
    E = r"(?:the\s+)?(?:element\s+)?"
    rules = [
        (r"^open\s+(.+)$", lambda m: f"Open {m.group(1)}"),
        (r"^wait\s+for\s+(?:the\s+)?page\s+to\s+load$", lambda m: "Wait for the page to load"),
        (r"^wait\s+until\s+" + E + r"(\S+)\s+is\s+visible$", lambda m: f"Wait for the {_r(m.group(1))} to appear"),
        (r"^wait\s+until\s+" + E + r"(\S+)\s+is\s+not\s+visible$", lambda m: f"Wait for the {_r(m.group(1))} to disappear"),
        (r"^verify\s+(?:that\s+)?" + E + r"(\S+)\s+is\s+visible$", lambda m: f"Check the {_r(m.group(1))} is shown"),
        (r"^verify\s+(?:that\s+)?" + E + r"(\S+)\s+is\s+not\s+visible$", lambda m: f"Check the {_r(m.group(1))} is not shown"),
        (r"^verify\s+(?:that\s+)?" + E + r"(\S+)\s+is\s+inside\s+(\S+)$",
         lambda m: f"Check the {_r(m.group(1))} sits inside the {_r(m.group(2))}"),
        (r"^verify\s+(?:that\s+)?" + E + r"(\S+)\s+(contains|equals|is)\s+\"(.*)\"$",
         lambda m: f"Check the {_r(m.group(1))} {'shows' if m.group(2) != 'contains' else 'contains'} \"{m.group(3)}\""),
        (r"^(?:click|tap)(?:\s+on)?\s+" + E + r"(\S+)$", lambda m: f"{tap} the {_r(m.group(1))}"),
        (r"^(?:double\s+tap|double\s+click)\s+" + E + r"(\S+)$", lambda m: f"Double-{tap.lower()} the {_r(m.group(1))}"),
        (r"^(?:enter|type)\s+(.+?)\s+(?:in|into)\s+" + E + r"(\S+)$", lambda m: f"Enter {m.group(1)} in the {_r(m.group(2))}"),
        (r"^go\s+back$", lambda m: "Go back to the previous page"),
        (r"^swipe\s+(left|right|up|down)$", lambda m: f"Swipe {m.group(1)}"),
        (r"^scroll\s+(?:down\s+)?(?:to|until)\s+" + E + r"(\S+)(?:\s+is\s+visible)?$", lambda m: f"Scroll to the {_r(m.group(1))}"),
        (r"^wait\s+(\d+)\s+seconds?$", lambda m: f"Wait {m.group(1)} seconds"),
        (r"^store\s+.*$", None),            # bookkeeping — not a user action
        (r"^take\s+screenshot.*$", None),
    ]
    for rx, fn in rules:
        m = re.match(rx, s, re.I)
        if m:
            return fn(m) if fn else ""
    t = re.sub(r"\b([a-z0-9]+(?:_[a-z0-9]+)+)\b", lambda x: _r(x.group(1)), s)
    return t[:1].upper() + t[1:]


def _secs(reason: str) -> str:
    m = re.search(r"within\s+(\d+)\s*ms|Timeout\s+(\d+)ms", reason or "")
    if not m:
        return ""
    ms = int(m.group(1) or m.group(2))
    return f"{ms // 1000} seconds" if ms >= 1000 else f"{ms} ms"


def _target_of(step: str) -> str:
    m = re.match(r"^(?:verify|wait\s+until|check)\s+(?:that\s+)?(?:the\s+)?(?:element\s+)?([A-Za-z_][\w.-]*)", (step or "").strip(), re.I)
    return m.group(1) if m and m.group(1).lower() not in ("url", "page", "stored", "title", "that") else ""


def _problem(step: str, reason: str) -> tuple[str, str, str]:
    """(summary phrase, expected, actual) for the failing step — in plain words."""
    s, r = (step or "").strip(), (reason or "")
    el = _target_of(s)
    name = _r(el) if el else ""
    secs = _secs(r)
    if re.search(r"\bis\s+not\s+visible$", s, re.I):
        return (f"{name[:1].upper() + name[1:]} is still shown",
                f"The {name} should not be shown.", f"The {name} is still shown.")
    if re.search(r"\bis\s+visible$", s, re.I) or "not become visible" in r or "not visible" in r.lower():
        return (f"{name[:1].upper() + name[1:]} not shown",
                f"The {name} should be shown.",
                f"The {name} did not appear" + (f" within {secs}." if secs else "."))
    m = re.search(r"\bis\s+inside\s+(\S+)$", s, re.I)
    if m:
        return (f"{name[:1].upper() + name[1:]} not placed inside the {_r(m.group(1))}",
                f"The {name} should sit inside the {_r(m.group(1))}.", plain_reason(r) + ".")
    m = re.search(r"\b(contains|equals|is)\s+\"(.*)\"$", s, re.I)
    if m and name:
        return (f"{name[:1].upper() + name[1:]} shows wrong text",
                f"The {name} should {'contain' if m.group(1) == 'contains' else 'show'} \"{m.group(2)}\".",
                plain_reason(r) + ".")
    if re.match(r"^verify\s+url", s, re.I):
        return ("Wrong page opened", step_plain(s) + ".", plain_reason(r) + ".")
    if re.match(r"^(?:click|tap)", s, re.I):
        tgt = re.sub(r"^(?:click|tap)(?:\s+on)?\s+(?:the\s+)?(?:element\s+)?", "", s, flags=re.I)
        return (f"{_r(tgt)[:1].upper() + _r(tgt)[1:]} cannot be tapped",
                f"It should be possible to tap the {_r(tgt)}.", plain_reason(r) + ".")
    short = _short(r, 90)
    return (short, step_plain(s) + " — should succeed.", plain_reason(r) + ".")


def _after(steps: list[str], idx: int, module: str) -> str:
    """'after tapping the PDP first photo' — the last user action before the failure."""
    for raw in reversed(steps[:idx]):
        s = raw.strip()
        m = re.match(r"^(?:click|tap)(?:\s+on)?\s+(?:the\s+)?(?:element\s+)?(\S+)$", s, re.I)
        if m:
            return f"after {'tapping' if _tap(module) == 'Tap' else 'clicking'} the {_r(m.group(1))}"
        m = re.match(r"^swipe\s+(\w+)$", s, re.I)
        if m:
            return f"after swiping {m.group(1)}"
        if re.match(r"^go\s+back$", s, re.I):
            return "after going back"
        m = re.match(r"^(?:enter|type)\s+.+?\s+(?:in|into)\s+(?:the\s+)?(\S+)$", s, re.I)
        if m:
            return f"after entering the {_r(m.group(1))}"
        if re.match(r"^open\s+", s, re.I):
            return "on page load"
    return ""


def _same_image(a: str, b: str) -> bool:
    """Two screenshots that show the same screen (only the label differs)."""
    try:
        from PIL import Image, ImageChops, ImageStat
        pa, pb = os.path.join(SCREENSHOTS_DIR, a), os.path.join(SCREENSHOTS_DIR, b)
        ia = Image.open(pa).convert("L").resize((64, 128))
        ib = Image.open(pb).convert("L").resize((64, 128))
        return ImageStat.Stat(ImageChops.difference(ia, ib)).mean[0] < 2.0
    except Exception:  # noqa: BLE001
        return False


def _ist_when(stamp: str) -> str:
    try:
        from datetime import datetime, timedelta, timezone
        t = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if t.tzinfo is not None:
            t = t.astimezone(timezone(timedelta(hours=5, minutes=30)))
        return f"{t.day} {t:%b %Y}, {t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'} IST"
    except Exception:  # noqa: BLE001
        return (stamp or "")[:16].replace("T", " ")


def _expected(step: str) -> str:
    s = step.strip()
    m = re.match(r"^verify\s+(?:that\s+)?(?:element\s+)?(.*)$", s, re.I)
    if m:
        text = re.sub(r"\b([a-z0-9]+(?:_[a-z0-9]+)+)\b", lambda x: _readable_name(x.group(1)), m.group(1))
        return text[0].upper() + text[1:]
    return f"The step completes: {s}"


def _human(test_case: str) -> str:
    return re.sub(r"_+", " ", test_case).strip()


def _readable_name(name: str) -> str:
    return re.sub(r"_+", " ", name).strip()


def plain_reason(reason: str) -> str:
    """The failure in a tester's words — no XPath, no call log."""
    r = (reason or "").strip()
    m = re.match(r"Visibility assertion failed for '([^']+)'", r)
    if m:
        return f"{_readable_name(m.group(1))} is not visible"
    m = re.match(r"'([^']+)' is not on the page", r)
    if m:
        return f"{_readable_name(m.group(1))} is not shown on the page"
    m = re.search(r"Timeout \d+ms exceeded.*?(?:waiting for|locator\()\s*['\"(]?([^'\")]+)", r, re.S)
    if m and "locator" in r.lower():
        return "The page did not show the expected element in time"
    r = re.sub(r"\s*\((?://|css=).*?\)\s*(?=:)", "", r)       # drop selectors
    r = re.split(r"\n\s*(?:Call log:|actual:|Actual value:)", r, flags=re.I)[0]
    return r.strip()


def _clean_actual(reason: str) -> str:
    r = re.sub(r"\s*\((?://|css=).*?\)\s*(?=:)", "", reason or "")
    r = re.split(r"\n\s*Call log:", r)[0]
    return r.strip()[:800]


def _short(reason: str, n: int = 110) -> str:
    reason = plain_reason(reason)
    first = re.split(r"(?<=[.!?])\s|\n", (reason or "").strip())[0]
    first = first.strip(" .")
    return first if len(first) <= n else first[:n - 1] + "…"


def _sources(run_id: str = "", plan_run: str = "") -> tuple[list[dict], dict]:
    """[{report, report_file, test_case, device_label, attempts, item}], context."""
    out: list[dict] = []
    ctx: dict = {}
    if plan_run:
        rec = _read_json(os.path.join(PLAN_RUNS_DIR, f"{os.path.basename(plan_run)}.json"))
        if not rec:
            raise IssueError(f"Plan run '{plan_run}' not found.")
        ctx = {"kind": "plan", "id": rec.get("id"), "name": rec.get("plan_name", ""),
               "site_env": (rec.get("execution") or {}).get("site_env", "")}
        for it in rec.get("items") or []:
            if it.get("status") != "failed" or not it.get("report_file"):
                continue
            rep = _report(it["report_file"])
            if rep:
                out.append({"report": rep, "report_file": it["report_file"],
                            "test_case": it.get("test_case") or rep.get("testplan", ""),
                            "device_label": (it.get("device_label") or _device_label(rep)).split(" — ")[0],
                            "attempts": it.get("attempts") or [], "suite": it.get("suite", ""),
                            "run_id": it.get("run_id", "")})
    else:
        from api.routes.tests import _find_report  # same lookup the report page uses
        path = _find_report(run_id)
        if not path:
            raise IssueError(f"Run '{run_id}' not found.")
        rep = _report(path)
        ctx = {"kind": "run", "id": run_id, "name": rep.get("testplan", "")}
        out.append({"report": rep, "report_file": os.path.basename(path),
                    "test_case": rep.get("testplan", ""), "device_label": _device_label(rep),
                    "attempts": [], "suite": "", "run_id": run_id})
    return out, ctx


def _device_label(rep: dict) -> str:
    d = rep.get("device") or {}
    ident = {"android_chrome": "Chrome on Android", "ios_safari": "Safari on iPhone",
             "samsung_internet": "Samsung Internet", "ios_chrome": "Chrome on iPhone"}.get(
        d.get("browser_identity") or "", "")
    name = d.get("device_name") or ""
    if ident and name:
        return f"{ident} ({name})"
    return ident or name or (d.get("browser") or "Desktop browser")


# ─────────────────────────────────────────────────────────────────────────────
# Drafting
# ─────────────────────────────────────────────────────────────────────────────
def type_rule(live: bool | None, confirmed: bool) -> tuple[str, list[str]]:
    """(default type, types the tester may pick) — the team rule."""
    if live:
        return ("Bug", ["Bug"]) if confirmed else ("Concern", ["Bug", "Concern"])
    return ("Defect" if confirmed else "Concern"), ["Defect", "Concern"]


def draft(run_id: str = "", plan_run: str = "") -> dict:
    sources, ctx = _sources(run_id, plan_run)
    raised = _read_json(RAISED_FILE)
    groups: dict[str, dict] = {}
    automation: list[dict] = []
    for src in sources:
        rep = src["report"]
        results = rep.get("results") or []
        site = _site(rep.get("site_env"))
        for idx, r in enumerate(results):
            if r.get("status") != "failed":
                continue
            step, reason = r.get("test_name", ""), r.get("reason", "")
            kind = classify(reason, step)
            if kind != "product":
                automation.append({"test_case": src["test_case"], "step": step,
                                   "reason": _short(reason, 200), "kind": kind,
                                   "device": src["device_label"], "report_file": src["report_file"]})
                continue
            gid = hashlib.sha1(f"{src['test_case']}|{step}|{_norm_reason(reason)}".encode()).hexdigest()[:12]
            g = groups.get(gid)
            if g is None:
                url = _first_url(results, site["host"])
                live = site["live"]
                if live is None:
                    host = (urlparse(url).hostname or "").lower() if url.startswith("http") else ""
                    live = host in ("www.justdial.com", "justdial.com") if host else None
                module = _module_of(src["test_case"], rep)
                raw_steps = [x.get("test_name", "") for x in results[:idx + 1]]
                g = groups[gid] = {
                    "id": gid, "test_case": src["test_case"], "suite": src.get("suite", ""),
                    "module": module, "after": _after(raw_steps, idx, module),
                    "plain_steps": [p for p in (step_plain(_resolve_display(x, site["host"]), module)
                                                for x in raw_steps[:-1]) if p],
                    "story": story_of(src["test_case"], ctx.get("name", ""), src.get("suite", "")),
                    "step": step, "reason": reason, "devices": [], "run_ids": [],
                    "screenshots": [], "video": "", "live": live, "url": url,
                    "site": site["name"] or (urlparse(url).hostname if url else "") or "as written",
                    "steps": [_resolve_display(x.get("test_name", ""), site["host"])
                              for x in results[:idx + 1]
                              if x.get("status") in ("passed", "failed")
                              and not (x.get("test_name", "").startswith("take screenshot"))],
                    "reproduced": [], "when": rep.get("started_at", ""),
                    "checking": _checking(src["test_case"]), "runs": [],
                    "context": {"kind": ctx.get("kind"), "name": ctx.get("name", ""),
                                "id": ctx.get("id", ""), "run_type": rep.get("run_type", ""),
                                "by": rep.get("triggered_by") or rep.get("executer", ""),
                                "suite": src.get("suite", "")},
                }
            if src["device_label"] not in g["devices"]:
                g["devices"].append(src["device_label"])
            dev = rep.get("device") or {}
            atts = src.get("attempts") or []
            g["runs"].append({
                "device": src["device_label"],
                "engine": {"chromium": "Chromium", "webkit": "WebKit (Safari engine)",
                           "firefox": "Firefox"}.get(dev.get("browser", ""), dev.get("browser", "")),
                "run_id": src.get("run_id") or rep.get("run_id", ""),
                "attempts": (f"failed {sum(1 for a in atts if a.get('failed'))} of {len(atts)} attempts"
                             if atts else "1 attempt"),
                "when": (rep.get("started_at") or "")[:16].replace("T", " ")})
            g["run_ids"].append(src.get("run_id") or rep.get("run_id", ""))
            before = results[idx - 1].get("screenshot") if idx else ""
            failing = r.get("screenshot")
            # The step before only helps when it shows a DIFFERENT screen; the same
            # screen twice (the page did not change) is one picture, not two.
            if before and failing and _same_image(before, failing):
                before = ""
            for shot in (before, failing):
                if shot and shot not in g["screenshots"]:
                    g["screenshots"].append(shot)
            if rep.get("video") and not g["video"]:
                g["video"] = rep["video"]
            fails = [a for a in src.get("attempts") or [] if a.get("failed")]
            if len(fails) >= 2:
                g["reproduced"].append(f"failed on the retry too ({src['device_label']})")

    issues = []
    for g in groups.values():
        if len(g["devices"]) >= 2:
            g["reproduced"].append(f"failed on {len(g['devices'])} devices")
        confirmed = bool(g["reproduced"])
        default, choices = type_rule(g["live"], confirmed)
        g.update(confirmed=confirmed, type=default, type_choices=choices, priority="Medium",
                 summary=_summary(g), description="", raised=raised.get(g["id"]))
        g["description"] = description(g)
        issues.append(g)
    issues.sort(key=lambda i: (not i["confirmed"], i["test_case"]))
    story = next((i["story"] for i in issues if i["story"]), "")
    return {"context": ctx, "story": story, "issues": issues, "automation": automation,
            "live": next((i["live"] for i in issues if i["live"] is not None), None)}


def _summary(g: dict) -> str:
    """'[Mobile Site] Gallery 360° icon not shown after tapping the PDP first photo'."""
    mod = _MODULE_LABEL.get(g.get("module") or "", "")
    phrase, _, _ = _problem(g["step"], g["reason"])
    after = g.get("after") or ""
    text = f"{phrase} {after}".strip()
    return (f"[{mod}] " if mod else "") + text[:1].upper() + text[1:]


def attachment_name(g: dict, rel: str) -> str:
    """'Chrome-on-Android-Pixel-7_step10_failing.jpg' instead of 'step_0010.jpg'."""
    run = next((r for r in g.get("runs") or [] if r.get("run_id") and r["run_id"] in rel), None)
    dev = re.sub(r"[^A-Za-z0-9]+", "-", (run or {}).get("device", "device")).strip("-")
    m = re.search(r"step_0*(\d+)", rel)
    n = m.group(1) if m else "x"
    shots = [x for x in g.get("screenshots") or [] if run and run["run_id"] in x]
    role = "failing" if shots and rel == shots[-1] else "before"
    return f"{dev}_step{n}_{role}{os.path.splitext(rel)[1] or '.jpg'}"


def _checking(test_case: str) -> str:
    """What the test case checks, from its '# Story:' / '# Purpose:' header."""
    h = _flow_header(test_case)
    text = h.get("story") or h.get("purpose") or ""
    text = re.sub(r"^\s*[A-Z][A-Z0-9]+-\d+\s*[—:-]*\s*", "", text).strip()
    return text[:1].upper() + text[1:]


def description(g: dict) -> str:
    """Jira wiki markup a developer can act on without opening the portal:
    the problem, where, every step to reproduce, expected / actual, how often,
    technical details, and the attachments."""
    mod = _MODULE_LABEL.get(g.get("module") or "", g.get("module") or "")
    phrase, expected, actual = _problem(g["step"], g["reason"])
    if g.get("after"):
        expected = expected.rstrip(".") + f" {g['after']}."
    lines = [f"*Problem:* {phrase} {g.get('after') or ''}".rstrip() + ".", ""]
    if g.get("checking"):
        lines += ["h3. What was being checked", g["checking"]
                  + (f" (story {g['story']})" if g.get("story") else ""), ""]
    lines += ["h3. Environment"]
    if mod:
        lines.append(f"* Module: {mod}")
    site = g.get("site") or "—"
    lines.append(f"* Server: {site}" + (" (live site)" if g.get("live") and "live" not in site else ""))
    if g.get("url"):
        lines.append(f"* Page URL: [{g['url']}]")
    lines += [f"* Device / browser: {d}" for d in g["devices"]]
    lines += [f"* When: {_ist_when(g.get('when') or '')}", "", "h3. Steps to reproduce"]
    steps = list(g.get("plain_steps") or [])
    if g.get("url") and not any(st.lower().startswith("open ") for st in steps):
        steps.insert(0, f"Open {g['url']}")
    for st in steps:
        lines.append(f"# {st}")
    lines.append(f"# *{step_plain(g['step'], g.get('module') or '')}*  ← fails here")
    lines += ["", "h3. Expected result", expected, "", "h3. Actual result", actual, ""]
    if g.get("confirmed"):
        lines += ["h3. How often", "Reproduced — " + "; ".join(g["reproduced"]) + "."]
    else:
        lines += ["h3. How often", "Seen once so far — please check."]
    runs = g.get("runs") or []
    if runs:
        lines += ["", "h3. Runs", "||Device||Browser engine||Result||Run||When||"]
        for r in runs:
            lines.append(f"|{r['device']}|{r['engine'] or '—'}|{r['attempts']}|{r['run_id'] or '—'}|{r['when']}|")
    c = g.get("context") or {}
    how = []
    if c.get("kind") == "plan":
        how.append(f"Test plan: {c.get('name')} (run {c.get('id')})")
    if c.get("suite"):
        how.append(f"Suite: {c['suite']}")
    how.append(f"Test case: {g['test_case']}")
    if c.get("run_type"):
        how.append(f"Run type: {c['run_type']}")
    if c.get("by"):
        how.append(f"Run by: {c['by']}")
    lines += ["", "h3. How it was run"] + [f"* {x}" for x in how]
    lines += ["", "h3. Technical details (for developers)",
              f"* Failing step as written: {{{{{g['step']}}}}}",
              "{noformat}" + _clean_actual(g["reason"]) + "{noformat}"]
    ev = []
    if g["screenshots"]:
        names = ", ".join(attachment_name(g, x) for x in g["screenshots"])
        ev.append((f"{len(g['screenshots'])} screenshots (the screen before and at the failure): "
                   if len(g["screenshots"]) > 1 else "Screenshot at the failure: ") + names)
    if g.get("video"):
        ev.append("Screen recording of the run: " + os.path.basename(g["video"]))
    if ev:
        lines += ["", "h3. Attachments"] + [f"* {x}" for x in ev]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Jira: project, duplicates, assignees
# ─────────────────────────────────────────────────────────────────────────────
def story_info(key: str) -> dict:
    s = _server()
    r = s.get(f"{base_url()}/rest/api/2/issue/{key}",
              params={"fields": "summary,project,issuetype,status"}, timeout=20)
    if r.status_code == 404:
        raise IssueError(f"Story {key} was not found in Jira.")
    r.raise_for_status()
    f = r.json()["fields"]
    return {"key": key, "summary": f.get("summary", ""), "type": f["issuetype"]["name"],
            "project": {"id": f["project"]["id"], "key": f["project"]["key"],
                        "name": f["project"].get("name", "")}}


def duplicates(story: str, issues: list[dict]) -> dict[str, list[dict]]:
    """Existing Bug/Defect/Concern tickets on the story that look like each issue."""
    if not story:
        return {}
    jql = (f'(issue in linkedIssues("{story}") OR labels = "{story}") '
           f'AND issuetype in (Bug, Defect, Concern)')
    r = _server().get(f"{base_url()}/rest/api/2/search",
                      params={"jql": jql, "fields": "summary,status,issuetype", "maxResults": 100},
                      timeout=25)
    if r.status_code >= 400:
        return {}
    found = [{"key": x["key"], "summary": x["fields"]["summary"],
              "status": x["fields"]["status"]["name"], "type": x["fields"]["issuetype"]["name"]}
             for x in r.json().get("issues", [])]

    def words(t):
        return {w for w in re.findall(r"[a-z0-9]{3,}", t.lower())
                if w not in {"the", "and", "for", "not", "photo", "photos", "icon", "page", "with"}}
    out = {}
    for i in issues:
        mine = words(i["summary"] + " " + i["reason"])
        hits = []
        for f in found:
            theirs = words(f["summary"])
            if mine and theirs and len(mine & theirs) / max(1, min(len(mine), len(theirs))) >= 0.4:
                hits.append(f)
        if hits:
            out[i["id"]] = hits[:3]
    return out


def assignable(project_key: str, query: str) -> list[dict]:
    r = _server().get(f"{base_url()}/rest/api/2/user/assignable/search",
                      params={"project": project_key, "username": query or "",
                              "maxResults": 20 if query else 300},
                      timeout=20)
    if r.status_code >= 400:
        return []
    return [{"name": u.get("name"), "display": u.get("displayName"), "email": u.get("emailAddress", "")}
            for u in r.json()]


# ─────────────────────────────────────────────────────────────────────────────
# Raising
# ─────────────────────────────────────────────────────────────────────────────
def _fields(issue: dict, story: dict) -> dict:
    t = issue.get("type") or "Defect"
    if t not in ISSUE_TYPES:
        raise IssueError(f"Type must be one of {', '.join(ISSUE_TYPES)}.")
    labels = [story["key"], DEFAULT_LABEL] + [x for x in issue.get("labels") or []
                                              if x and x not in (story["key"], DEFAULT_LABEL)]
    f = {"project": {"id": story["project"]["id"]}, "issuetype": {"name": t},
         "summary": (issue.get("summary") or "").strip()[:250],
         "description": issue.get("description") or "",
         "priority": {"name": issue.get("priority") or "Medium"},
         "labels": [re.sub(r"\s+", "_", x) for x in labels]}
    if not f["summary"]:
        raise IssueError("Every issue needs a summary.")
    owner = issue.get("owner") or ""
    if isinstance(owner, dict):
        owner = owner.get("name", "")
    if owner:
        f["assignee"] = {"name": owner}
    if t == "Defect" and issue.get("devices"):
        f["environment"] = f"{issue.get('site', '')} — " + ", ".join(issue["devices"])
    return f


def _attachments(issue: dict) -> list[tuple[str, str]]:
    """[(path on disk, name shown in Jira)]."""
    out = []
    for rel in issue.get("screenshots") or []:
        p = os.path.join(SCREENSHOTS_DIR, rel)
        if os.path.isfile(p):
            out.append((p, attachment_name(issue, rel)))
    if issue.get("video"):
        p = os.path.join(DATA_DIR, issue["video"])
        if os.path.isfile(p) and os.path.getsize(p) <= MAX_ATTACH_BYTES:
            out.append((p, os.path.basename(p)))
    return out


def raise_issues(username: str, issues: list[dict], story_key: str) -> list[dict]:
    """Create each ticket, link it to the story, attach the evidence. Per-item result."""
    token = _user_token(username)
    if not token:
        raise IssueError("Connect your Jira account first (your own token), or use "
                         "'Open in Jira' / 'Download CSV'.")
    if not story_key:
        raise IssueError("Which story are these for? Enter the story ticket.")
    story = story_info(story_key)
    s = _session(token)
    results = []
    raised = _read_json(RAISED_FILE)
    for issue in issues:
        iid = issue.get("id", "")
        if iid and raised.get(iid):
            results.append({"id": iid, "ok": True, "already": True, **raised[iid]})
            continue
        try:
            fields = _fields(issue, story)
            r = s.post(f"{base_url()}/rest/api/2/issue", json={"fields": fields}, timeout=30)
            if r.status_code >= 400:
                raise IssueError(_jira_error(r))
            key = r.json()["key"]
            warn = []
            lr = s.post(f"{base_url()}/rest/api/2/issueLink", timeout=20, json={
                "type": {"name": "Relates"}, "inwardIssue": {"key": key},
                "outwardIssue": {"key": story_key}})
            if lr.status_code >= 400:
                warn.append("not linked to the story")
            for p, shown in _attachments(issue):
                with open(p, "rb") as fh:
                    ar = s.post(f"{base_url()}/rest/api/2/issue/{key}/attachments",
                                headers={"X-Atlassian-Token": "no-check"},
                                files={"file": (shown, fh)}, timeout=60)
                if ar.status_code >= 400:
                    warn.append(f"{shown} not attached")
            rec = {"key": key, "url": f"{base_url()}/browse/{key}", "type": fields["issuetype"]["name"],
                   "by": username, "at": _dt.datetime.now().isoformat(timespec="seconds")}
            if iid:
                with _lock:
                    raised = _read_json(RAISED_FILE)
                    raised[iid] = rec
                    _write_json(RAISED_FILE, raised)
            results.append({"id": iid, "ok": True, **rec, "warnings": warn})
        except IssueError as e:
            results.append({"id": iid, "ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            results.append({"id": iid, "ok": False, "error": f"Jira error: {e}"})
    return results


def _jira_error(r) -> str:
    try:
        j = r.json()
        msgs = list((j.get("errors") or {}).values()) + list(j.get("errorMessages") or [])
        if msgs:
            return "Jira refused: " + "; ".join(str(m) for m in msgs)
    except Exception:  # noqa: BLE001
        pass
    return f"Jira refused ({r.status_code})."


_TYPE_IDS = {"Bug": "10102", "Defect": "10500", "Concern": "11300"}
_PRIORITY_IDS = {"Highest": "1", "High": "2", "Medium": "3", "Low": "4", "Lowest": "5"}


def prefill_url(issue: dict, project_id: str, story_key: str) -> str:
    """Jira's own Create form, filled in — for testers without a saved token.
    Screenshots cannot travel in a link: the form says to attach them."""
    desc = (issue.get("description") or "")
    if len(desc) > 1800:
        desc = desc[:1800] + "\n…(shortened — see the portal report for the rest)"
    desc += "\n\nPlease attach the screenshots from the portal report."
    q = {"pid": project_id, "issuetype": _TYPE_IDS.get(issue.get("type") or "Defect", "10500"),
         "summary": (issue.get("summary") or "")[:250], "description": desc,
         "priority": _PRIORITY_IDS.get(issue.get("priority") or "Medium", "3"),
         "labels": [story_key, DEFAULT_LABEL] if story_key else [DEFAULT_LABEL]}
    owner = issue.get("owner")
    if isinstance(owner, dict):
        owner = owner.get("name")
    if owner:
        q["assignee"] = owner
    return f"{base_url()}/secure/CreateIssueDetails!init.jspa?{urlencode(q, doseq=True)}"


def csv_bytes(issues: list[dict], project_key: str, story_key: str) -> bytes:
    """Jira CSV import file (System → External System Import → CSV)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Project Key", "Issue Type", "Summary", "Priority", "Assignee", "Labels",
                "Description", "Linked story", "Screenshots (attach after import)"])
    for i in issues:
        owner = i.get("owner") or ""
        if isinstance(owner, dict):
            owner = owner.get("name", "")
        w.writerow([project_key, i.get("type") or "Defect", i.get("summary", ""),
                    i.get("priority") or "Medium", owner,
                    " ".join(x for x in (story_key, DEFAULT_LABEL) if x), i.get("description", ""),
                    story_key, "; ".join(i.get("screenshots") or [])])
    return buf.getvalue().encode("utf-8-sig")
