"""
execution/test_data.py — values a test uses, kept out of the test.

Two kinds, deliberately separated:

  GLOBAL      One value, every platform, every environment. A test mobile
              number, a static OTP, a username. Marking something global is a
              statement that it does not vary — so a flow written on the website
              and the same flow on the mobile site draw the same number.

  PER-ENVIRONMENT   The same name resolves differently depending on where you
              are pointing: base_url is staging2.justdial.com on staging and
              www.justdial.com on live. The flow says ${base_url} either way.

Why a value is never written into a step
----------------------------------------
Picking a value in the editor inserts `${test_mobile}`, not "9324751210". The
step then reads the same on screen for everyone, the number can be changed in
one place, and — the reason that matters most — a mobile number, OTP or password
never lands in a .flow file, a report, a screenshot or the repository. The editor
shows the value in masked form (93---210) so you can still tell WHICH number you
picked without the digits being on screen or in the file.

Anything whose NAME looks like a secret (password, token, api_key …) is refused
a stored value entirely: it is declared here so flows can reference it and the
value comes from .env at run time. config.settings.is_secret_name decides, so
this file and the run logs agree about what counts as a secret.
"""
from __future__ import annotations

import json
import os
import threading

from config import settings

#: The file the linter already reads for its "global" section, so a value added
#: here is immediately one flow_lint stops reporting as undefined.
PATH = os.path.join(settings.BASE_DIR, "data", "common", "variables.json")

_LOCK = threading.Lock()

_EMPTY = {"_comment": "Global runtime variables — managed via the Test Data screen",
          "global": {}, "env": {"local": {}, "staging": {}, "cloud": {}},
          "dataset_mappings": {}}


def _read() -> dict:
    """Load the store. A missing or unreadable file is an empty one, never a crash."""
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(_EMPTY))
    if not isinstance(data, dict):
        return json.loads(json.dumps(_EMPTY))
    data.setdefault("global", {})
    data.setdefault("env", {})
    if not isinstance(data["global"], dict):
        data["global"] = {}
    if not isinstance(data["env"], dict):
        data["env"] = {}
    return data


def _write(data: dict) -> None:
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PATH)          # atomic, so a crash mid-write cannot truncate it


def mask(value: str) -> str:
    """
    The on-screen form of a value: 9324751210 -> 93---210.

    Enough to recognise which value you picked, not enough to read off a shared
    screen or a screenshot. Short values are masked harder, because showing two
    of four characters gives most of it away.
    """
    v = str(value or "")
    if not v:
        return ""
    if len(v) <= 4:
        return "•" * len(v)
    if len(v) <= 8:
        return v[0] + "•" * (len(v) - 2) + v[-1]
    return f"{v[:2]}---{v[-3:]}"


def environments() -> list[str]:
    return sorted(_read().get("env", {}))


def get_all(environment: str = "") -> dict:
    """
    Every declared value, with provenance and a masked display form.

    Returns {name: {scope, environment, value, display, is_secret, defined}}.
    A secret's `value` is never included — only whether .env currently supplies
    one — so this response is safe to render, log or screenshot.
    """
    data = _read()
    out: dict[str, dict] = {}

    for name, value in (data.get("global") or {}).items():
        if str(name).startswith("_"):
            continue
        out[name] = _entry(name, value, scope="global", environment="")

    if environment:
        for name, value in (data.get("env", {}).get(environment) or {}).items():
            if str(name).startswith("_"):
                continue
            # A per-environment value overrides a global of the same name: it is
            # the more specific statement about where you are pointing.
            out[name] = _entry(name, value, scope="environment", environment=environment)
    return out


def rows(environment: str = "") -> list:
    """
    One entry per stored value, as a LIST rather than a name-keyed dict.

    `environment="*"` spans every environment. That cannot be expressed as a
    dict keyed by name — base_url exists in staging AND in cloud, and one of
    them would silently win, which is exactly the collapse the screen is meant
    to let you see through.
    """
    data = _read()
    out = [_entry(n, v, scope="global", environment="")
           for n, v in (data.get("global") or {}).items()
           if not str(n).startswith("_")]
    envs = (list((data.get("env") or {}).keys()) if environment == "*"
            else ([environment] if environment else []))
    for env in envs:
        out += [_entry(n, v, scope="environment", environment=env)
                for n, v in ((data.get("env", {}).get(env) or {}).items())
                if not str(n).startswith("_")]
    return out


def resolved(environment: str = "") -> dict:
    """
    {name: value} for everything that actually HAS a value right now.

    get_all() is the display view: it never carries a credential's value, so a
    caller that seeds a run from it silently supplies nothing for exactly the
    values that matter most. This is the run view — a credential is looked up in
    the environment here, and anything still empty is left out entirely so it
    reads as "not supplied" rather than as an empty string that overwrites a
    real value later.
    """
    out: dict[str, str] = {}
    for name, entry in get_all(environment).items():
        if entry.get("is_secret"):
            value = os.getenv(name.upper()) or os.getenv(name) or ""
        else:
            value = str(entry.get("value") or "")
        if value:
            out[name] = value
    return out


def _entry(name: str, value, scope: str, environment: str) -> dict:
    # Storage is refused only for true credentials. An OTP or a mobile number is
    # ordinary test data: stored here, masked on screen, masked in logs.
    secret = settings.is_credential_name(name)
    if secret:
        # Declared so a flow can reference it; the value lives in .env only.
        supplied = bool(os.getenv(name.upper()) or os.getenv(name))
        return {"name": name, "scope": scope, "environment": environment,
                "value": "", "display": "from .env" if supplied else "not set in .env",
                "is_secret": True, "defined": supplied}
    return {"name": name, "scope": scope, "environment": environment,
            "value": str(value), "display": mask(value),
            "is_secret": False, "defined": str(value) != ""}


def set_value(name: str, value: str, *, scope: str = "global",
              environment: str = "") -> dict:
    """
    Declare or update one value.

    A secret-looking name is accepted as a DECLARATION but its value is dropped:
    writing a password into a repository file is the thing this module exists to
    prevent, and silently storing it would be worse than refusing.
    """
    from nlp.variables import normalise

    clean = normalise(name)
    if not clean:
        raise ValueError(f"{name!r} is not a usable variable name.")

    with _LOCK:
        data = _read()
        if settings.is_credential_name(clean):
            stored, note = "", ("Declared. Its value must come from .env — "
                                "secrets are never written to this file.")
        else:
            stored, note = str(value), ""

        if scope == "environment":
            if not environment:
                raise ValueError("An environment is required for an environment value.")
            data.setdefault("env", {}).setdefault(environment, {})[clean] = stored
        else:
            data.setdefault("global", {})[clean] = stored
        _write(data)

    return {"name": clean, "scope": scope, "environment": environment,
            "display": mask(stored) if stored else ("from .env"
                                                    if settings.is_credential_name(clean) else ""),
            "note": note}


def delete_value(name: str, *, scope: str = "global", environment: str = "") -> bool:
    with _LOCK:
        data = _read()
        if scope == "environment":
            bucket = data.get("env", {}).get(environment, {})
        else:
            bucket = data.get("global", {})
        if name not in bucket:
            return False
        del bucket[name]
        _write(data)
    return True


def suggestions(partial: str, environment: str = "", limit: int = 12) -> list[dict]:
    """
    Values matching `partial`, by NAME or by the value itself.

    Matching on the value is what makes "type 93 and pick the number" work: you
    remember the number, not what it was called. The digits never leave this
    process — the caller receives only the masked form and the reference to
    insert.
    """
    p = (partial or "").strip().lower()
    rows: list[dict] = []
    for name, e in get_all(environment).items():
        if p and p not in name.lower() and not (e["value"] and e["value"].lower().startswith(p)):
            continue
        rows.append({"name": name,
                     "insert": "${" + name + "}",
                     "display": e["display"],
                     "scope": e["scope"],
                     "tag": "g" if e["scope"] == "global" else (e["environment"] or "env"),
                     "is_secret": e["is_secret"],
                     "defined": e["defined"]})
        if len(rows) >= limit:
            break
    rows.sort(key=lambda r: (r["scope"] != "global", r["name"]))
    return rows
