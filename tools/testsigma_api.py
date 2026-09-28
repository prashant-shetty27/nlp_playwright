"""
tools/testsigma_api.py — read-only client for the Testsigma REST API.

The key is read from .env (TESTSIGMA_KEY or TESTSIGMA_API_KEY) on the machine
running the portal and is never logged, returned or written anywhere.
Only GET requests are made: this tool reads test cases, it never changes
anything in Testsigma.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

import config.settings  # noqa: F401 — loads .env


class TestsigmaError(RuntimeError):
    pass


def _key() -> str:
    k = (os.getenv("TESTSIGMA_KEY") or os.getenv("TESTSIGMA_API_KEY") or "").strip().strip("'\"")
    if not k:
        raise TestsigmaError("Add TESTSIGMA_KEY=<your API key> to .env (Testsigma → Settings → API Keys).")
    return k


def base_url() -> str:
    return (os.getenv("TESTSIGMA_BASE_URL") or "https://app-in.testsigma.com").rstrip("/")


def get(path: str, params: dict | None = None, timeout: int = 60):
    """GET <base>/<path> and return parsed JSON (or text). Raises TestsigmaError."""
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/api/"):
        raise TestsigmaError("Only /api/… paths are allowed.")
    url = base_url() + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    key = _key()
    # Reports (e.g. /api/v1/reports/junit/<run id>) are XML; asking for JSON gets 406.
    accept = "application/xml, text/xml, */*" if "/reports/" in path else "application/json"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500].replace(key, "<key>")
        raise TestsigmaError(f"Testsigma answered {e.code} for {path}: {detail}") from e
    except Exception as e:  # noqa: BLE001
        raise TestsigmaError(f"Could not reach Testsigma ({base_url()}): {str(e)[:200]}") from e
    try:
        return json.loads(body)
    except ValueError:
        return body
