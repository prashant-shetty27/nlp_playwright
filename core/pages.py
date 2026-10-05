"""
core/pages.py — the page knowledge base (data/pages.json).

What a page type is (URL patterns), what proves it rendered (landmark elements),
which popups appear on it (closers) and its sections top→bottom. Read live, so
edits in Settings → Pages apply to the next step.

    detect(url)            -> page key ("pdp", "prp", …) or ""
    landmarks(key)         -> element names that prove the page rendered
    popups(key)            -> closer element names for that page
    page_for_url(url)      -> the whole entry
"""
from __future__ import annotations

import json
import os
import re
import time

from config.settings import DATA_DIR

PATH = os.path.join(DATA_DIR, "pages.json")
_cache: dict = {"mtime": 0.0, "data": {}}


def load() -> dict:
    try:
        mt = os.path.getmtime(PATH)
    except OSError:
        return {}
    if mt != _cache["mtime"]:
        try:
            with open(PATH, "r", encoding="utf-8") as f:
                _cache["data"] = (json.load(f) or {}).get("pages") or {}
        except (OSError, ValueError):
            _cache["data"] = {}
        _cache["mtime"] = mt
    return _cache["data"]


def save(pages: dict) -> None:
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = {}
    doc["pages"] = pages
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PATH)


def detect(url: str) -> str:
    """Most specific page whose URL pattern matches (longest pattern wins)."""
    best, best_len = "", -1
    for key, p in load().items():
        for pat in (p.get("detect") or {}).get("url") or []:
            try:
                if re.search(pat, url or "", re.I) and len(pat) > best_len:
                    best, best_len = key, len(pat)
            except re.error:
                continue
    return best


def page_for_url(url: str) -> dict:
    return load().get(detect(url)) or {}


def landmarks(key: str) -> list[str]:
    return list((load().get(key) or {}).get("landmark") or [])


def popups(key: str) -> list[str]:
    return list((load().get(key) or {}).get("popups") or [])


def sections(key: str) -> list[str]:
    return list((load().get(key) or {}).get("sections") or [])


def wait_for_page(page, url: str, timeout_ms: int = 15000) -> str:
    """After `open`: wait until one of the page's landmarks is visible (any of
    them — a result page may show products OR 'no results'). Returns the page
    key, or "" when the URL is not a known page (then nothing is waited for).
    Never fails the step: the next step decides what it needs."""
    key = detect(url)
    names = landmarks(key)
    if not names:
        return key
    from locators.manager import get_locator_and_dna
    sels = []
    for n in names:
        try:
            x, _ = get_locator_and_dna(n)
            if x:
                sels.append(x)
        except Exception:  # noqa: BLE001
            pass
    if not sels:
        return key
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for x in sels:
            try:
                if page.locator(x).first.is_visible(timeout=200):
                    return key
            except Exception:  # noqa: BLE001
                pass
        page.wait_for_timeout(250)
    return key
