#!/usr/bin/env python3
"""
tools/check_http_auth.py — which way of answering a Basic-auth prompt works.

Run this from the machine and network that can actually reach the site:

    python3 tools/check_http_auth.py https://staging2.justdial.com/rne/JDM42UP7BK/JA

A Basic-auth prompt is drawn by the BROWSER, not the page. No locator reaches it
and no `type into` step fills it — the credentials have to be attached before the
first navigation. There are three ways to do that and they are NOT equivalent,
because which one works is a property of the server:

  no credentials     the control. If this already loads, the prompt is not HTTP
                     Basic auth at all and none of the rest is the answer.
  context, on 401    Playwright's default. Waits for the server to answer 401
                     with WWW-Authenticate, then retries. Useless against a
                     server that drops the connection instead of challenging.
  context, always    sends Authorization preemptively on every request.
  in the URL         https://user:pass@host — what this project does by default.
                     Proven, but Chromium keeps narrowing where it is honoured.

Nothing here is written down and no credential is printed: the output names the
method and the HTTP status only.

Whichever wins, put it in the run:

  in the URL      nothing to do — it is the default
  context/always  POST /tests/run with {"http_auth_domain": "<host>"}
  context/401     ... plus {"http_auth_send": "unauthorized"}
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config.settings import get_auth_registry  # noqa: E402

TIMEOUT_MS = 25_000


def _probe(label: str, url: str, context_kwargs: dict) -> None:
    """Navigate once and report the status, or the reason it never got one."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_context(**context_kwargs).new_page()
            response = page.goto(url, wait_until="domcontentloaded",
                                 timeout=TIMEOUT_MS)
            status = response.status if response else 0
            verdict = "WORKS" if 200 <= status < 400 else "blocked"
            print(f"  {label:<22} HTTP {status:<4} {verdict}")
        except Exception as e:  # noqa: BLE001 — every failure is a result here
            # The URL is never echoed: on the embedded-credentials probe it
            # carries the password, and Playwright puts it in the message.
            first = str(e).splitlines()[0]
            for secret in _SECRETS:
                first = first.replace(secret, "<redacted>")
            print(f"  {label:<22} {'—':<9} failed: {first[:88]}")
        finally:
            browser.close()


_SECRETS: list[str] = []


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip())
        return 2
    url = sys.argv[1]
    host = urlparse(url).netloc.split("@")[-1]

    registry = get_auth_registry()
    creds = registry.get(host)
    if not creds:
        print(f"No credentials registered for {host!r}.")
        print(f"Registered domains: {', '.join(registry) or 'none'}")
        print("Add AUTH_<NAME>_DOMAIN / _USERNAME / _PASSWORD to .env.")
        return 1
    _SECRETS.extend([creds["password"], creds["username"]])

    print(f"\n{host} — credentials found in .env (value withheld)\n")
    _probe("no credentials", url, {})
    _probe("context, on 401", url, {"http_credentials": {**creds,
                                                         "send": "unauthorized"}})
    _probe("context, always", url, {"http_credentials": {**creds,
                                                         "send": "always"}})

    embedded = urlparse(url)._replace(
        netloc=f"{creds['username']}:{creds['password']}@{host}").geturl()
    _probe("in the URL", embedded, {})

    print("\nWhichever says WORKS is the one to use — see the notes at the top "
          "of this file for how to switch a run to it.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
