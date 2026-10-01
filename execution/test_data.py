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

#: Values the runner fills in by itself — never asked for, never stored.
#:   otp  static OTP for a test number on this platform, else fetched from the OTP portal
AUTOMATIC = {"otp"}

_EMPTY = {"_comment": "Global runtime variables — managed via the Test Data screen",
          "global": {}, "env": {"local": {}, "staging": {}, "cloud": {}},
          "dataset_mappings": {}}


def _read(strict: bool = False) -> dict:
    """Load the store. A missing file is an empty one.

    strict (used before every write): a file that exists but is not valid JSON
    raises instead of reading as empty — otherwise one save after a hand-edit
    typo wrote back an EMPTY store and every saved value was gone.
    """
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return json.loads(json.dumps(_EMPTY))
    except (OSError, json.JSONDecodeError) as e:
        if strict:
            raise ValueError(f"Test Data file {PATH} could not be read ({e}). Fix or restore it "
                             f"(a backup is kept as variables.json.bak) before saving.") from e
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
    try:                    # last good copy, in case a value is deleted by mistake
        import shutil
        if os.path.exists(PATH):
            shutil.copy2(PATH, PATH + ".bak")
    except OSError:
        pass
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


# ── modules ───────────────────────────────────────────────────────────────────
# Each value is tagged with the modules (website, mobilesite, android, ios,
# hybrid) it belongs to — by default the module it was created in. A value is
# listed, suggested and supplied to a run only in its own modules; tagging it
# with several modules is how one value is shared. Tags live beside the values
# ("modules": {name: [...]}) so the value storage itself is unchanged.
MODULES = ("website", "mobilesite", "android", "ios", "hybrid")


def modules_of(name: str, data: dict | None = None) -> list[str]:
    """The modules a value is tagged with; an untagged value belongs to all."""
    tags = ((data or _read()).get("modules") or {}).get(name)
    return [m for m in tags if m in MODULES] if isinstance(tags, list) and tags else list(MODULES)


def _in_module(name: str, module: str | None, data: dict) -> bool:
    return not module or module not in MODULES or module in modules_of(name, data)


def set_modules(name: str, modules: list[str]) -> list[str]:
    """Tag a value with the modules it belongs to (at least one)."""
    clean = [m for m in dict.fromkeys(str(x).lower() for x in modules or []) if m in MODULES]
    if not clean:
        raise ValueError("Pick at least one module for this value.")
    with _LOCK:
        data = _read(strict=True)
        data.setdefault("modules", {})[name] = clean
        _write(data)
    return clean


def environments() -> list[str]:
    return sorted(_read().get("env", {}))


def get_all(environment: str = "", module: str | None = None) -> dict:
    """
    Every declared value, with provenance and a masked display form.

    Returns {name: {scope, environment, value, display, is_secret, defined}}.
    A secret's `value` is never included — only whether .env currently supplies
    one — so this response is safe to render, log or screenshot.
    """
    data = _read()
    out: dict[str, dict] = {}

    for name, value in (data.get("global") or {}).items():
        if str(name).startswith("_") or not _in_module(name, module, data):
            continue
        out[name] = _entry(name, value, scope="global", environment="", data=data)

    if environment:
        for name, value in (data.get("env", {}).get(environment) or {}).items():
            if str(name).startswith("_") or not _in_module(name, module, data):
                continue
            # A per-environment value overrides a global of the same name: it is
            # the more specific statement about where you are pointing.
            out[name] = _entry(name, value, scope="environment", environment=environment, data=data)
    return out


def rows(environment: str = "", module: str | None = None) -> list:
    """
    One entry per stored value, as a LIST rather than a name-keyed dict.

    `environment="*"` spans every environment. That cannot be expressed as a
    dict keyed by name — base_url exists in staging AND in cloud, and one of
    them would silently win, which is exactly the collapse the screen is meant
    to let you see through.
    """
    data = _read()
    out = [_entry(n, v, scope="global", environment="", data=data)
           for n, v in (data.get("global") or {}).items()
           if not str(n).startswith("_") and _in_module(n, module, data)]
    envs = (list((data.get("env") or {}).keys()) if environment == "*"
            else ([environment] if environment else []))
    for env in envs:
        out += [_entry(n, v, scope="environment", environment=env, data=data)
                for n, v in ((data.get("env", {}).get(env) or {}).items())
                if not str(n).startswith("_") and _in_module(n, module, data)]
    return out


def resolved(environment: str = "", module: str | None = None) -> dict:
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
    for name, entry in get_all(environment, module).items():
        if entry.get("is_secret"):
            value = os.getenv(name.upper()) or os.getenv(name) or ""
        else:
            value = str(entry.get("value") or "")
        if value:
            out[name] = value
    return out


def _entry(name: str, value, scope: str, environment: str, data: dict | None = None) -> dict:
    e = _entry_core(name, value, scope, environment)
    e["modules"] = modules_of(name, data)
    return e


def _entry_core(name: str, value, scope: str, environment: str) -> dict:
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
              environment: str = "", modules: list[str] | None = None,
              retag: bool = False) -> dict:
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
        data = _read(strict=True)
        existed = clean in (data.get("global") or {}) or any(
            clean in (b or {}) for b in (data.get("env") or {}).values())
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
        tags = [m for m in dict.fromkeys(str(x).lower() for x in (modules or [])) if m in MODULES]
        # A new value belongs to the module it is created in; an existing one keeps
        # its tags unless they are being changed on purpose (Test Data screen).
        if tags and (retag or not existed):
            data.setdefault("modules", {})[clean] = tags
        _write(data)

    return {"name": clean, "scope": scope, "environment": environment,
            "display": mask(stored) if stored else ("from .env"
                                                    if settings.is_credential_name(clean) else ""),
            "note": note}


def delete_value(name: str, *, scope: str = "global", environment: str = "") -> bool:
    with _LOCK:
        data = _read(strict=True)
        if scope == "environment":
            bucket = data.get("env", {}).get(environment, {})
        else:
            bucket = data.get("global", {})
        if name not in bucket:
            return False
        del bucket[name]
        still = name in (data.get("global") or {}) or any(
            name in (b or {}) for b in (data.get("env") or {}).values())
        if not still:
            (data.get("modules") or {}).pop(name, None)
        _write(data)
    return True


def suggestions(partial: str, environment: str = "", limit: int = 12,
                module: str | None = None) -> list[dict]:
    """
    Values matching `partial`, by NAME or by the value itself.

    Matching on the value is what makes "type 93 and pick the number" work: you
    remember the number, not what it was called. The digits never leave this
    process — the caller receives only the masked form and the reference to
    insert.
    """
    p = (partial or "").strip().lower()
    rows: list[dict] = []
    for name, e in get_all(environment, module).items():
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
