"""
config/environment_manager.py
──────────────────────────────────────────────────────────────────────────────
Manages test environments: active env, domain substitution, and auth injection.

Key features:
  - List / get / set / save / delete environments
  - Domain replacement: swap www.example.com → staging.example.com across ALL
    URL steps in a flow, at any scope (plan / suite / single flow)
  - Auth injection: HTTP Basic (credentials in URL), or popup credentials stored
    for runner to handle a JavaScript login dialog

Usage in runner.py:
    from config.environment_manager import transform_url, get_active_env
    resolved_url = transform_url(original_url)

Usage in UI:
    from config import environment_manager as em
    em.list_envs()           → ["local", "staging", "production"]
    em.set_active_env("staging")
    env = em.get_active_env()
"""

import json
import os
import re
from urllib.parse import urlparse, urlunparse

ENV_FILE = os.path.join(os.path.dirname(__file__), "environments.json")

_DEFAULT_ENV = {
    "base_url": "",
    "domain_override": "",
    "domain_source": "",
    "auth_type": "none",
    "username": "",
    "password": "",
    "notes": "",
}

_DEFAULT_DATA = {
    "active": "local",
    "envs": {
        "local": dict(_DEFAULT_ENV, notes="Default — runs URLs exactly as recorded"),
    },
}


# ─── Low-level I/O ────────────────────────────────────────────────────────────

def _load() -> dict:
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Back-fill missing top-level keys
            for k, v in _DEFAULT_DATA.items():
                data.setdefault(k, v)
            return data
        except Exception:
            pass
    return dict(_DEFAULT_DATA)


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ─── Public API ───────────────────────────────────────────────────────────────

def list_envs() -> list:
    return list(_load()["envs"].keys())


def get_active_env_name() -> str:
    return _load().get("active", "local")


def get_env(name: str) -> dict:
    data = _load()
    env = data["envs"].get(name, {})
    return dict(_DEFAULT_ENV, **env)  # merge defaults


def get_active_env() -> dict:
    data = _load()
    name = data.get("active", "local")
    env  = data["envs"].get(name, {})
    return dict(_DEFAULT_ENV, **env)


def set_active_env(name: str) -> None:
    data = _load()
    if name not in data["envs"]:
        raise ValueError(f"Environment '{name}' not found. Available: {list(data['envs'])}")
    data["active"] = name
    _save(data)


def save_env(name: str, env_dict: dict) -> None:
    """Create or update a named environment."""
    data = _load()
    data["envs"][name] = dict(_DEFAULT_ENV, **env_dict)
    _save(data)


def delete_env(name: str) -> bool:
    data = _load()
    if name not in data["envs"]:
        return False
    del data["envs"][name]
    if data.get("active") == name:
        data["active"] = "local"
    _save(data)
    return True


# ─── URL Transformation ───────────────────────────────────────────────────────

def apply_domain_replacement(url: str, env: dict | None = None) -> str:
    """
    Replace the host/domain in a URL based on the active environment config.

    Logic:
      1. If `domain_override` is set  → replace scheme+host with override.
         If `domain_source` is also set → only replace when domain_source matches.
      2. Elif `base_url` is set and `domain_source` is set → same host-swap.
      3. Elif `base_url` is set (no domain_source) → replace scheme+host for ALL urls.
      4. Otherwise → return unchanged.

    Examples:
      domain_source="www.justdial.com", domain_override="staging2.justdial.com"
      "https://www.justdial.com/Mumbai/Beauty-Parlours/..."
       → "https://staging2.justdial.com/Mumbai/Beauty-Parlours/..."
    """
    if env is None:
        env = get_active_env()
    if not env:
        return url

    domain_override = (env.get("domain_override") or "").strip()
    domain_source   = (env.get("domain_source")   or "").strip()
    base_url        = (env.get("base_url")         or "").strip()

    # Nothing configured → pass through
    if not domain_override and not base_url:
        return url

    try:
        parsed = urlparse(url)
        current_host = parsed.netloc.lower()

        # ── Case 1: explicit domain_override ──────────────────────────────────
        if domain_override:
            # If domain_source is set, only rewrite matching URLs
            if domain_source and domain_source.lower() not in current_host:
                return url
            # Parse override to extract host (and optional port)
            ov = domain_override if "://" in domain_override else "https://" + domain_override
            ov_parsed = urlparse(ov)
            new_scheme = ov_parsed.scheme or parsed.scheme
            new_netloc = ov_parsed.netloc
            return urlunparse((
                new_scheme, new_netloc,
                parsed.path, parsed.params, parsed.query, parsed.fragment
            ))

        # ── Case 2 / 3: base_url only ─────────────────────────────────────────
        if base_url:
            bp = base_url if "://" in base_url else "https://" + base_url
            bp_parsed = urlparse(bp)
            if domain_source and domain_source.lower() not in current_host:
                return url
            new_scheme = bp_parsed.scheme or parsed.scheme
            new_netloc = bp_parsed.netloc
            return urlunparse((
                new_scheme, new_netloc,
                parsed.path, parsed.params, parsed.query, parsed.fragment
            ))

    except Exception:
        pass
    return url


def inject_auth(url: str, env: dict | None = None) -> str:
    """
    Inject HTTP Basic Auth credentials into the URL when auth_type == "basic".

    Produces: https://username:password@staging.example.com/path
    The browser/Playwright will use these to pass the HTTP Basic challenge.
    For JS popup dialogs see get_popup_credentials().
    """
    if env is None:
        env = get_active_env()
    auth_type = (env.get("auth_type") or "none").lower()
    username  = (env.get("username")  or "").strip()
    password  = (env.get("password")  or "").strip()

    if auth_type == "basic" and username:
        try:
            parsed = urlparse(url)
            creds  = f"{username}:{password}" if password else username
            new_netloc = f"{creds}@{parsed.netloc}"
            return urlunparse((
                parsed.scheme, new_netloc,
                parsed.path, parsed.params, parsed.query, parsed.fragment
            ))
        except Exception:
            pass
    return url


def get_popup_credentials(env: dict | None = None) -> tuple:
    """Return (username, password) for JavaScript login popups. '' if not set."""
    if env is None:
        env = get_active_env()
    if (env.get("auth_type") or "none").lower() in ("popup", "js_popup", "basic"):
        return (env.get("username") or "", env.get("password") or "")
    return ("", "")


def transform_url(url: str, env: dict | None = None) -> str:
    """Apply domain replacement then optional auth injection. Single call from runners."""
    url = apply_domain_replacement(url, env)
    url = inject_auth(url, env)
    return url


def apply_to_flow_steps(steps: list, env: dict | None = None) -> list:
    """
    Rewrite all `go to url ...` steps in a list of step strings.
    Returns a new list — original is not mutated.
    Used when running a flow file to apply domain/auth transformation.
    """
    if env is None:
        env = get_active_env()
    out = []
    for step in steps:
        s = step.strip()
        m = re.match(
            r'^((?:go\s+to\s+url|navigate\s+to|browse\s+to|visit|open\s+url|open)\s+)(https?://\S+)$',
            s, re.I
        )
        if m:
            new_url = transform_url(m.group(2), env)
            out.append(f"{m.group(1)}{new_url}")
        else:
            out.append(step)
    return out


def site_environments() -> list[dict]:
    """
    Environments a run can target: [{"name", "host", "needs_login"}], names only
    — never credentials. From config/environments.json entries that replace
    www.justdial.com with another host (prot, prot3, devx …).
    """
    from urllib.parse import urlparse
    out = []
    for name, env in (_load().get("envs") or {}).items():
        host = (env.get("domain_override") or "").strip()
        if not host and env.get("base_url") and env.get("domain_source"):
            host = urlparse(env["base_url"]).hostname or ""
        host = host.replace("https://", "").replace("http://", "").strip("/")
        if not host or "example.com" in host:
            continue
        out.append({"name": name, "host": host,
                    "needs_login": (env.get("auth_type") or "none").lower() == "basic"})
    out = sorted(out, key=lambda e: e["name"])
    # "live" is always offered and always means www.justdial.com: a test written
    # with prot3 / staging addresses runs on the live site when it is chosen.
    # (Choosing nothing keeps every address exactly as written in the test.)
    if not any(e["host"].lower() == "www.justdial.com" for e in out):
        out.insert(0, {"name": "live", "host": "www.justdial.com", "needs_login": False})
    return out
