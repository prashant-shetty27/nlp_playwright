"""
execution/otp_portal.py — read a live OTP from the internal QA OTP portal.

Normal usage is one line, because the OTP step always follows mobile submission:

    click submit                                the app sends the SMS
    fetch otp for "${mobile}" as otp            read it
    enter otp "${otp}" into otp_input

The portal shows the latest message matched for a number and carries no
timestamp, so a read cannot itself prove the code is new. That is accepted: if a
stale code is read, OTP verification fails on the page like any other assertion
and the failure is investigated normally. A short settle gives the SMS time to
land first, which handles the common case without inventing machinery.

For the rare flow that must not tolerate a stale read, the optional `after`
clause takes the value seen before submission and polls until the portal shows
something different.

The portal is a different site from the application under test, so it is driven
in its own browser context. The flow's own page is never navigated away from.
"""
from __future__ import annotations

import logging
import os
import re
import time

logger = logging.getLogger(__name__)

OTP_RE = re.compile(r"\b(\d{4,8})\b")

# Selectors are the portal's, verified live 2026-08-18.
SEL_USERNAME = "#username"
SEL_PIN = "#pin"
SEL_SUBMIT = "button[type=submit]"
SEL_MOBILE = "#otpMobile"
SEL_FETCH = "#fetchOtpBtn"
SEL_OTP = "span.otp-highlight"


class OtpPortalError(RuntimeError):
    """Raised with a cause the operator can act on — never with the OTP in it."""


def _config() -> tuple[str, str, str]:
    url = os.getenv("OTP_PORTAL_URL", "").strip()
    user = os.getenv("OTP_PORTAL_USERNAME", "").strip()
    pin = os.getenv("OTP_PORTAL_PIN", "").strip()
    missing = [n for n, v in (("OTP_PORTAL_URL", url), ("OTP_PORTAL_USERNAME", user),
                              ("OTP_PORTAL_PIN", pin)) if not v]
    if missing:
        raise OtpPortalError(
            "The OTP portal is not configured. Set "
            + ", ".join(missing)
            + " in .env. (The access code changes — see the portal's login page.)"
        )
    return url, user, pin


def _read_current(page) -> str:
    """The OTP the portal is showing right now, or '' if none is displayed."""
    try:
        loc = page.locator(SEL_OTP)
        if loc.count() == 0:
            return ""
        text = (loc.first.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    m = OTP_RE.search(text)
    return m.group(1) if m else ""


def fetch_otp(browser, mobile: str, *, after: str = "", timeout_s: int = 60,
              poll_s: float = 3.0, settle_ms: int = 4000) -> str:
    """
    Return the OTP the portal holds for `mobile`.

    Waits `settle_ms` before the first read so a just-sent SMS has time to arrive;
    without it the portal is queried faster than the message can land and returns
    the previous one.

    `after`, when given, is the value seen before submission — the read then polls
    until the portal shows something different. Omitted (the normal case) the
    first code displayed is returned.

    `browser` is a live Playwright Browser. A dedicated context is created and
    closed here so the flow's own page and cookies are untouched.
    """
    url, user, pin = _config()
    mobile = re.sub(r"\D", "", str(mobile or ""))
    if len(mobile) != 10:
        raise OtpPortalError(
            f"A 10-digit mobile number is required to fetch an OTP; got {len(mobile)} digit(s)."
        )

    context = browser.new_context()
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if page.locator(SEL_PIN).count():
            page.fill(SEL_USERNAME, user)
            page.fill(SEL_PIN, pin)          # value never logged
            page.click(SEL_SUBMIT)
            page.wait_for_timeout(2000)
        if page.locator(SEL_MOBILE).count() == 0:
            raise OtpPortalError(
                "Signed in to the OTP portal but the fetch form did not appear — "
                "the access code may be wrong or expired."
            )

        # Give the SMS a moment to arrive before the first read.
        page.wait_for_timeout(max(0, int(settle_ms)))

        deadline = time.time() + max(1, int(timeout_s))
        attempts = 0
        while True:
            attempts += 1
            page.fill(SEL_MOBILE, mobile)
            page.click(SEL_FETCH)
            page.wait_for_timeout(2500)
            current = _read_current(page)

            if current and (not after or current != after):
                logger.info("📩 OTP read from the portal (attempt %d)%s.",
                            attempts, ", confirmed new" if after else "")
                return current

            if time.time() >= deadline:
                if after and current:
                    raise OtpPortalError(
                        f"No NEW OTP arrived for the test number within {timeout_s}s "
                        f"({attempts} attempts). The portal still shows the same message "
                        f"as before the request, so the SMS likely was not sent."
                    )
                raise OtpPortalError(
                    f"No OTP was displayed for the test number within {timeout_s}s "
                    f"({attempts} attempts)."
                )
            time.sleep(poll_s)
    finally:
        try:
            context.close()
        except Exception:  # noqa: BLE001
            pass
