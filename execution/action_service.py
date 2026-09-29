"""
execution/action_service.py
All Playwright action functions + @codeless_snippet registry bindings.
Extracted from actions.py with updated imports pointing to the new modules.

Global state (RUNTIME_VARIABLES) now lives in nlp.variable_manager.
"""
import re
import threading
import time
import logging
import os

from playwright.sync_api import expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from execution.retry import with_retry
from execution.browser_manager import (
    _ensure_dir, _timestamp, get_standard_timeout_ms, get_default_scroll_count,
)
from execution.session import TestSession
from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables
from locators.manager import (get_alternate_selectors, get_locator_and_dna,
                              promote_selector)
from core.healer import ml_heal_element, confirm_heal
from core.registry import codeless_snippet
from config.settings import SITES
from config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# TAB / WINDOW & IFRAME STATE (session-scoped)
# ─────────────────────────────────────────────────────────────────────────────
class _ThreadSession:
    """
    The run session of the CURRENT thread, reached through the one module name.

    This used to be a single global: Run Center and plan runs never re-bound
    it, so a tab / iframe a flow switched to stayed "active" after its browser
    closed and every later run failed with "page has been closed" until a
    restart — and two runs at once drove each other's tabs. Each run thread
    now has its own; code that reads or sets `_TEST_SESSION.active_page` is
    unchanged.
    """
    __slots__ = ()
    _local = threading.local()

    def _get(self) -> TestSession:
        sess = getattr(_ThreadSession._local, "session", None)
        if sess is None:
            sess = TestSession()
            _ThreadSession._local.session = sess
        return sess

    def __getattr__(self, name):
        return getattr(self._get(), name)

    def __setattr__(self, name, value):
        setattr(self._get(), name, value)


_TEST_SESSION = _ThreadSession()


def set_test_session(session: TestSession | None) -> None:
    """Bind action-service state to the active run session (for this thread)."""
    _ThreadSession._local.session = session or TestSession()


def get_active_page(default_page):
    """Return the active tab page; falls back to the runner's page if no tab switch happened."""
    return _TEST_SESSION.active_page if _TEST_SESSION.active_page is not None else default_page


def _get_locator_root(page):
    """Return the iframe FrameLocator when inside one, else the active page."""
    if _TEST_SESSION.active_frame is not None:
        return _TEST_SESSION.active_frame
    return get_active_page(page)


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _parse_boolean(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ('true', 'yes', '1', 'y')


def _resolve_live(page, locator_name):
    """
    Resolve a locator against the page as it actually is right now.

    Returns (selector, dna) exactly like get_locator_and_dna, so every caller's
    existing recovery path is untouched — the only difference is WHICH selector
    they start from. When the stored primary matches nothing and the entry has
    recorded alternates, the alternates are tried in order and the winner is
    promoted so the next run starts there.

    Entries with a single recorded selector skip the probe entirely, so this is a
    no-op for every locator captured before alternates existed.
    """
    primary, dna = get_locator_and_dna(locator_name)
    if not primary:
        raise Exception(f"Locator '{locator_name}' not found in any page.")
    primary = resolve_variables(primary)

    alts = get_alternate_selectors(locator_name)
    if not alts:
        return primary, dna

    try:
        if _get_locator_root(page).locator(primary).count() > 0:
            return primary, dna
    except Exception:
        pass   # malformed/stale selector counts as a miss — fall through to alternates

    for alt in alts:
        candidate = resolve_variables(alt["value"])
        try:
            if _get_locator_root(page).locator(candidate).count() > 0:
                logger.warning("🔁 Primary selector for '%s' matched nothing; using recorded "
                               "alternate %r", locator_name, candidate)
                # Writing the alternate back as primary is a change to the
                # locator database — it follows the same HEAL_MEMORY switch as
                # every other write-back (TODO.md §1). Off: the alternate is
                # used for this step only.
                from locators import healing_memory as _hm
                if _hm.enabled():
                    promote_selector(locator_name, candidate, alt.get("type") or "css")
                return candidate, dna
        except Exception:
            continue

    # Nothing matched. Hand back the primary so the caller's own recovery
    # (innerText retry, ML heal) still runs and reports against the real name.
    return primary, dna


def _stabilize_page(page):
    """
    Architectural barrier: waits for SPA/React routing and network stabilization.
    """
    # Condition-based: continue as soon as the network is quiet, and never
    # wait longer than STEP_SETTLE_MS. The old fixed 1.5 s sleep cost a
    # 30-step case ~45 s whether the page was busy or not.
    budget = max(0, int(settings.STEP_SETTLE_MS))
    started = time.perf_counter()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    if settings.STEP_SETTLE_FIXED:
        # A value set by hand is a fixed wait, exactly as written.
        page.wait_for_timeout(budget)
        return
    remaining = budget - int((time.perf_counter() - started) * 1000)
    if remaining > 0:
        try:
            page.wait_for_load_state("networkidle", timeout=remaining)
        except Exception:  # noqa: BLE001 — still busy at the budget: carry on
            pass


def _get_healed_element_locator(page, locator_name):
    primary_xpath, dna = _resolve_live(page, locator_name)
    root = _get_locator_root(page)   # frame-aware: uses iframe context when active
    loc = root.locator(primary_xpath).first

    # is_visible() does not wait (its timeout argument is ignored), so healing
    # used to fire on elements that had simply not rendered yet — and could
    # verify, then promote, the wrong element. Wait for it first.
    try:
        loc.wait_for(state="visible", timeout=3000)
        visible = True
    except Exception:  # noqa: BLE001
        visible = False
    if not visible:
        logger.warning("Verification element not visible after 3 s. Attempting ML heal...")
        if dna:
            try:
                healed_xpath = ml_heal_element(page, dna, locator_name)  # scans the real DOM
                if healed_xpath:
                    healed_loc = root.locator(healed_xpath).first
                    # Proven only once the healed node is actually there and
                    # visible; the caller's own check then runs against it.
                    healed_loc.wait_for(state="visible", timeout=3000)
                    confirm_heal(locator_name)
                    logger.info("🏥 Healed verification element '%s' via %s", locator_name, healed_xpath)
                    return healed_loc
            except Exception:
                pass
    return loc


# ─────────────────────────────────────────────────────────────────────────────
# OPEN SITE
# ─────────────────────────────────────────────────────────────────────────────
@with_retry(max_attempts=3, delay=2.0)
def _reachability_hint(host: str) -> str:
    """
    Say WHY a navigation never got a response, when the network can answer that.

    ERR_CONNECTION_CLOSED against an internal address reads as a tool failure
    and is not one: the host resolved, the TCP connection opened, and the far
    end dropped it. Naming the address range turns "the automation is broken"
    into "you are not on the VPN", which is the actual next action and is not
    otherwise visible from a run report.
    """
    import ipaddress
    import socket

    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except Exception:  # noqa: BLE001
        return (f"'{host}' could not be resolved at all — check the spelling, "
                f"or whether it only exists on an internal network. ")

    # 100.64.0.0/10 is shared address space: carrier NAT and, in practice, the
    # range corporate VPNs hand out. Python calls it neither private nor global.
    shared = ip in ipaddress.ip_network("100.64.0.0/10")
    if ip.is_private or shared:
        return (f"'{host}' resolves to {ip}, which is an internal address — it "
                f"is only routable from inside the corporate network. The name "
                f"resolved and the connection opened, then the site closed it. "
                f"Check the VPN is connected; if it is and the page opens in your "
                f"own browser, the site briefly refused the automated browser — "
                f"run again. ")
    return ""


def open_site(page, url: str):
    from urllib.parse import urlparse
    from config.settings import get_auth_registry

    if not url or not isinstance(url, str):
        raise ValueError("Validation Error: 'url' parameter must be a non-empty string.")

    raw_url = url.strip()

    # Resolve site aliases (e.g. "justdial" → "https://www.justdial.com")
    raw_url = SITES.get(raw_url.lower(), raw_url)

    if " " in raw_url:
        raise ValueError(f"Validation Error: URL cannot contain spaces. Received: '{raw_url}'")
    if "." not in raw_url:
        raise ValueError(
            f"Routing Error: '{raw_url}' is not a known site alias and lacks a domain structure."
        )

    sanitized_url = raw_url if raw_url.startswith(("http://", "https://")) else f"https://{raw_url}"
    parsed_url = urlparse(sanitized_url)
    # hostname, NOT netloc. netloc carries any `user:password@` the author wrote
    # into the step, so using it as the domain meant three things went wrong at
    # once: the auth registry never matched, DNS was asked to resolve
    # "user:pass@host", and the "safe" URL built for logs and error messages
    # carried the password — the one place that was supposed to be clean.
    target_domain = (parsed_url.hostname or "").lower()
    if parsed_url.port:
        target_domain = f"{target_domain}:{parsed_url.port}"
    #: What the author typed, credentials included — needed only to rebuild the
    #: URL, never to display, log or resolve.
    supplied_netloc = parsed_url.netloc

    if not target_domain:
        raise ValueError(f"Validation Error: '{sanitized_url}' could not be parsed into a valid domain.")

    # ── Authentication ────────────────────────────────────────────────────────
    # Preferred path: the browser context already carries HTTP Basic credentials
    # (see browser_manager.apply_http_credentials). Nothing is added to the URL,
    # so no credential can reach a log line, an exception or a report.
    from execution.browser_manager import CONTEXT_AUTH_DOMAINS

    auth_registry = get_auth_registry()
    if target_domain in CONTEXT_AUTH_DOMAINS:
        logger.info("🔒 '%s' is authenticated at the browser-context level; "
                    "no credentials placed in the URL.", target_domain)
    elif target_domain in auth_registry:
        credentials = auth_registry[target_domain]
        username = credentials.get("username")
        password = credentials.get("password")
        if not username or not password:
            raise ValueError(f"Security Error: Incomplete credentials for domain '{target_domain}'.")
        logger.warning(
            "⚠️  '%s' has registered credentials but the browser context was not created "
            "with them. Falling back to URL-embedded HTTP Basic auth. Prefer setting "
            "\"http_auth_domain\" in the suite's desired_capabilities.", target_domain
        )
        parsed_url = parsed_url._replace(netloc=f"{username}:{password}@{target_domain}")
        sanitized_url = parsed_url.geturl()
        supplied_netloc = parsed_url.netloc

    # Never log or raise with a credential-bearing URL.
    safe_url = f"{parsed_url.scheme}://{target_domain}{parsed_url.path}"
    logger.info(f"🌐 Navigating to: {safe_url}")
    try:
        page.goto(sanitized_url, wait_until="domcontentloaded", timeout=30000)
        try:
            # Capped at 5 s: ad / tracking beacons keep a Justdial page from ever
            # going idle, so this used to burn its full 10 s on most opens. The
            # next step waits for what it actually needs (result page / element).
            page.wait_for_load_state("networkidle",
                                     timeout=int(os.getenv("OPEN_IDLE_TIMEOUT_MS", "5000")))
        except Exception:
            pass  # networkidle timeout is non-fatal
    except Exception as e:
        # Playwright reports a failure with the URL it was GIVEN, credentials
        # and all. This line built a safe_url for its own text and then pasted
        # the raw exception after it, so any failed navigation to a domain in
        # the auth registry wrote the password into the run log and the report.
        detail = str(e)
        if sanitized_url != safe_url:
            detail = detail.replace(sanitized_url, safe_url)
        if "@" in supplied_netloc:
            detail = detail.replace(supplied_netloc, target_domain)
        raise RuntimeError(f"Navigation Error: Failed to load '{safe_url}'. "
                           f"{_reachability_hint(parsed_url.hostname or '')}"
                           f"Details: {detail}")


# ─────────────────────────────────────────────────────────────────────────────
# CLICK
# ─────────────────────────────────────────────────────────────────────────────
@with_retry(max_attempts=2, delay=1.0)
def click_element(page, locator_name):
    primary_xpath, dna = _resolve_live(page, locator_name)

    try:
        logger.info(f"🖱️ Attempting click on: {locator_name}")
        target = _get_locator_root(page).locator(primary_xpath).first
        try:
            is_select = target.evaluate("el => el.tagName === 'SELECT'", timeout=2000)
        except (PlaywrightTimeoutError, PlaywrightError, TypeError):
            is_select = False
        if is_select:
            # A real click on a <select> opens the browser's own option list, a
            # native window that no step can close — and while it is open the
            # page cannot be photographed, so the run hung. Focus it instead;
            # "select option … in …" picks the value.
            target.focus(timeout=5000)
            logger.info("✅ '%s' is a dropdown — focused (use 'select option' to pick).", locator_name)
            return
        target.click(timeout=5000)
        logger.info("✅ Click successful.")
        _stabilize_page(page)
    except (PlaywrightTimeoutError, PlaywrightError):
        # ── Second chance: innerText exact-text locator ───────────────────────
        inner_text = (dna or {}).get("innerText", "") if dna else ""
        inner_text = (inner_text or "").strip()
        if inner_text:
            try:
                logger.info("🔤 Primary XPath failed — retrying via innerText: '%s'", inner_text)
                page.get_by_text(inner_text, exact=True).first.click(timeout=4000)
                logger.info("✅ Click via innerText successful.")
                _stabilize_page(page)
                return
            except (PlaywrightTimeoutError, PlaywrightError):
                logger.warning("⚠️ innerText fallback also failed. Triggering ML Healer...")
        else:
            logger.warning("⚠️ Primary locator failed. Triggering ML Healer...")

        # ── Third chance: ML self-healer ──────────────────────────────────────
        if not dna:
            raise Exception(f"Element broken and no ML DNA available: {locator_name}")
        try:
            healed_xpath = ml_heal_element(page, dna, locator_name)
        except Exception as ml_err:
            raise Exception(f"Self-healing match failed: {ml_err}")

        if healed_xpath:
            _get_locator_root(page).locator(healed_xpath).first.click(timeout=5000)
            confirm_heal(locator_name)
            logger.info(f"🏥 Successfully healed and clicked '{locator_name}' via {healed_xpath}")
            _stabilize_page(page)
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")


# ─────────────────────────────────────────────────────────────────────────────
# FILL
# ─────────────────────────────────────────────────────────────────────────────
@with_retry(max_attempts=2, delay=1.0)
def fill_element(page, text, locator_name):
    primary_xpath, dna = _resolve_live(page, locator_name)

    def execute_robust_fill(xpath):
        """
        Put `text` in the field and PROVE it landed.

        Three strategies, weakest assumption last, each checked by reading the
        value back. Without that read-back a fill could report success while the
        field stayed empty — the run carried on to the submit, and the first
        anyone knew was the site's own "enter mobile number" alert several steps
        later, blamed on the wrong step.
        """
        loc = _get_locator_root(page).locator(xpath).first
        want = str(text)

        def landed() -> bool:
            try:
                return loc.input_value(timeout=1500) == want
            except PlaywrightError:
                # Not an <input>; fall back to whatever the node holds.
                try:
                    return (loc.evaluate("el => el.value ?? el.textContent") or "") == want
                except PlaywrightError:
                    return False

        try:
            loc.fill(want, timeout=3000)
            if landed():
                return True
            logger.warning("⚠️  fill() left '%s' holding %r — retyping as keystrokes.",
                           locator_name, "<hidden>" if settings.is_secret_name(locator_name)
                           else loc.input_value(timeout=1000))
        except PlaywrightTimeoutError:
            if loc.count() == 0:
                raise
            logger.warning("🛡️ Input field blocked. Trying real keystrokes...")

        # Real keystrokes. A field that filters input per key, or an app that
        # tracks its own copy of the value from key events, ignores a value set
        # in one go — the DOM shows the number and the app still believes the
        # field is empty.
        try:
            loc.click(timeout=2000)
            loc.press_sequentially(want, delay=20, timeout=5000)
            if landed():
                return True
        except PlaywrightError as e:
            logger.warning("⚠️  Keystroke entry failed for '%s': %s", locator_name, e)

        # Last resort: set the value through the NATIVE setter and fire the
        # events a framework listens for. Assigning el.value directly is what
        # React's value tracker ignores, so the field showed the number and the
        # component's state never changed.
        logger.warning("🛡️ Forcing value via JavaScript for '%s'...", locator_name)
        loc.evaluate(
            """(el, v) => {
                const proto = el instanceof HTMLTextAreaElement
                    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
                setter ? setter.call(el, v) : (el.value = v);
                el.dispatchEvent(new Event('input',  {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""", want)
        if landed():
            return True
        raise AssertionError(
            f"❌ Could not put {'<hidden>' if settings.is_secret_name(locator_name) else repr(want)} "
            f"into '{locator_name}'. The field is "
            f"present but its value did not change — it may be read-only, "
            f"covered by an overlay, or reset by the page as fast as it is set.")

    # An OTP / mobile / password typed into a field named like one is never
    # written to the log — the same rule the reports already apply.
    shown = "<hidden>" if settings.is_secret_name(locator_name) else text
    try:
        logger.info(f"⌨️ Attempting to type '{shown}' into: {locator_name}")
        execute_robust_fill(primary_xpath)
        logger.info("✅ Fill successful.")
        _stabilize_page(page)
    except (PlaywrightTimeoutError, PlaywrightError):
        logger.warning("⚠️ Primary input failed. Triggering ML Healer...")
        if not dna:
            raise Exception(f"Element broken and no ML DNA available to heal: {locator_name}")
        try:
            healed_xpath = ml_heal_element(page, dna, locator_name)
        except Exception as ml_err:
            raise Exception(f"Self-healing match failed: {ml_err}")

        if healed_xpath:
            execute_robust_fill(healed_xpath)
            confirm_heal(locator_name)
            logger.info(f"🏥 Successfully healed and filled '{locator_name}' via {healed_xpath}")
            _stabilize_page(page)
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────
def extract_element_text(page, locator_name, variable_name):
    primary_xpath, dna = _resolve_live(page, locator_name)

    def execute_extraction(xpath):
        loc = _get_locator_root(page).locator(xpath).first
        extracted_text = loc.inner_text(timeout=5000).strip()
        RUNTIME_VARIABLES[variable_name] = extracted_text
        logger.info(f"💾 EXTRACTED: '{extracted_text}' -> Stored as '${variable_name}'")
        return True

    try:
        logger.info(f"📄 Attempting to read text from: {locator_name}")
        execute_extraction(primary_xpath)
    except (PlaywrightTimeoutError, PlaywrightError):
        logger.warning("⚠️ Primary read failed. Triggering ML Healer...")
        if not dna:
            raise Exception(f"Element broken and no ML DNA available: {locator_name}")
        healed_xpath = ml_heal_element(page, dna, locator_name)
        if healed_xpath:
            execute_extraction(healed_xpath)
            confirm_heal(locator_name)
            logger.info(f"🏥 Successfully healed and extracted text from '{locator_name}' via {healed_xpath}")
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")


def extract_element_attribute(page, locator_name, attribute_name, variable_name):
    loc = _get_healed_element_locator(page, locator_name)
    val = loc.get_attribute(attribute_name)
    if val is None:
        logger.warning(f"⚠️ Attribute '{attribute_name}' not found on '{locator_name}'. Storing empty string.")
        val = ""
    RUNTIME_VARIABLES[variable_name] = str(val).strip()
    logger.info(f"💾 EXTRACTED ATTRIBUTE: '{val}' -> Stored as '${variable_name}'")


def extract_input_value(page, locator_name, variable_name):
    loc = _get_healed_element_locator(page, locator_name)
    val = loc.input_value(timeout=5000)
    RUNTIME_VARIABLES[variable_name] = str(val).strip()
    logger.info(f"💾 EXTRACTED INPUT: '{val}' -> Stored as '${variable_name}'")


def extract_element_count(page, locator_name, variable_name):
    primary_xpath, _ = _resolve_live(page, locator_name)
    count = _get_locator_root(page).locator(primary_xpath).count()
    RUNTIME_VARIABLES[variable_name] = str(count)
    logger.info(f"💾 EXTRACTED COUNT: {count} elements found -> Stored as '${variable_name}'")


def _strip_credentials(url: str) -> str:
    """
    Drop any user:password@ from a URL.

    After URL-embedded Basic auth the browser's own `page.url` carries the
    credentials, so every later read of "where are we" — a stored variable, a
    log line, a tab listing — was a place the password could surface. The
    address is still useful without them; the credentials never are.
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(str(url or ""))
        if "@" in parsed.netloc:
            return parsed._replace(netloc=parsed.netloc.split("@", 1)[1]).geturl()
    except Exception:  # noqa: BLE001 — a URL we cannot parse is returned as-is
        pass
    return str(url or "")


def extract_page_url(page, variable_name):
    url = _strip_credentials(page.url)
    RUNTIME_VARIABLES[variable_name] = str(url)
    logger.info(f"💾 EXTRACTED URL: '{url}' -> Stored as '${variable_name}'")


def extract_regex(page, pattern: str, source: str, variable_name: str) -> None:
    """
    `store regex "<pattern>" from <var> as <new_var>`
    `store regex "<pattern>" from page url as <new_var>`

    The first capture group (or the whole match, when the pattern has none)
    of `pattern` applied to the source text. This is how a step reads the
    city, the category id or the search term out of the URL it opened, so an
    API call can be built for THAT search instead of carrying values a person
    typed in — `store regex "nct-(\\d+)" from page url as ncatid`.
    """
    if source.strip().lower() in ("page url", "url"):
        text = _strip_credentials(page.url)
        origin = "page url"
    else:
        if source not in RUNTIME_VARIABLES:
            raise Exception(f"❌ Variable '${{{source}}}' is not stored in memory.")
        text = str(RUNTIME_VARIABLES[source])
        origin = f"${{{source}}}"
    pat = resolve_variables(pattern)
    try:
        m = re.search(pat, text)
    except re.error as e:
        raise Exception(f"❌ Bad regex {pat!r}: {e}") from e
    if not m:
        raise Exception(f"❌ Regex {pat!r} matched nothing in {origin}: '{text[:160]}'")
    value = m.group(1) if m.groups() else m.group(0)
    RUNTIME_VARIABLES[variable_name] = value
    logger.info("💾 REGEX %r on %s -> ${%s} = %r", pat, origin, variable_name, value)


def transform_text(mode: str, source: str, variable_name: str) -> None:
    """
    `store lowercase of "<text or ${var}>" as <var>`   (also uppercase, trimmed)
    A value read from the page or a URL is often needed in another case for
    an API call — lead_gen wants the city in lowercase while the URL has it
    capitalised. Plain text and ${references} are both accepted.
    """
    text = resolve_variables(source)
    if source.strip() in RUNTIME_VARIABLES and not source.strip().startswith("${"):
        text = str(RUNTIME_VARIABLES[source.strip()])
    out = {"lowercase": text.lower(), "uppercase": text.upper(),
           "trimmed": text.strip()}[mode]
    RUNTIME_VARIABLES[variable_name] = out
    logger.info("💾 %s of %r -> ${%s} = %r", mode, text[:60], variable_name, out[:60])


def extract_page_title(page, variable_name):
    title = page.title()
    RUNTIME_VARIABLES[variable_name] = str(title)
    logger.info(f"💾 EXTRACTED TITLE: '{title}' -> Stored as '${variable_name}'")


def create_custom_variable(value, variable_name):
    val = resolve_variables(str(value))
    RUNTIME_VARIABLES[variable_name] = val
    logger.info(f"💾 CREATED VARIABLE: '{val}' -> Stored as '${variable_name}'")


# ─────────────────────────────────────────────────────────────────────────────
# MODAL DISMISSAL HELPER  (used by search and any future action that needs it)
# ─────────────────────────────────────────────────────────────────────────────
def _dismiss_modal(page_obj, wait_for_popup_ms: int | None = None):
    """
    Waits for a blocking modal (e.g. JustDial login popup) and dismisses it.

    Strategy (in priority order):
      1. Use the stored `maybe_later_link` manual locator (XPath from locators_manual.json)
      2. Try common close-button CSS selectors as a fallback
      3. Press Escape
      4. Force-hide via JavaScript
    """
    # Give the popup time to appear (JustDial fires it ~5 s after page load)
    # — but only as long as it takes: poll for any known modal selector and
    # stop waiting the moment one shows, up to SEARCH_MODAL_WAIT_MS.
    budget = int(settings.SEARCH_MODAL_WAIT_MS if wait_for_popup_ms is None else wait_for_popup_ms)
    MODAL_LOCATOR_NAMES = ["maybe_later_link"]
    probe = [xp for xp in (get_locator_and_dna(n)[0] for n in MODAL_LOCATOR_NAMES) if xp] + \
            ["#login-modal", "#loginPop", ".jd_modal", "//a[@aria-label='May be later']"]
    deadline = time.perf_counter() + budget / 1000
    appeared = False
    if settings.SEARCH_MODAL_WAIT_FIXED and wait_for_popup_ms is None:
        # A value set by hand is a fixed wait, exactly as written.
        page_obj.wait_for_timeout(budget)
        appeared = True          # then dismiss whatever is there, as before
    while not appeared and time.perf_counter() < deadline:
        for sel in probe:
            try:
                if page_obj.locator(sel).first.is_visible():
                    appeared = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if appeared:
            break
        page_obj.wait_for_timeout(250)
    if not appeared:
        logger.info("ℹ️  No login popup within %d ms — continuing.", budget)
        return

    # ── 1. Try the manual locator ─────────────────────────────────────────────
    for locator_name in MODAL_LOCATOR_NAMES:
        xpath, _ = get_locator_and_dna(locator_name)
        if xpath:
            try:
                el = page_obj.locator(xpath).first
                if el.is_visible(timeout=1500):
                    el.click(timeout=2000)
                    logger.info("🚫 Modal dismissed via manual locator '%s'", locator_name)
                    page_obj.wait_for_timeout(500)
                    return
            except Exception as e:
                logger.debug("Manual locator '%s' not clickable: %s", locator_name, e)

    # ── 2. Generic close-button CSS fallback ─────────────────────────────────
    FALLBACK_SELECTORS = [
        "//a[@aria-label='May be later']",         # JustDial — direct XPath
        "//button[@aria-label='May be later']",
        "#loginPop .close",
        "#login-modal [aria-label*='lose' i]",
        "button.modal-close",
        ".jd_modal .close",
        "[data-dismiss='modal']",
    ]
    for sel in FALLBACK_SELECTORS:
        try:
            el = page_obj.locator(sel).first
            if el.is_visible(timeout=600):
                el.click(timeout=1500)
                logger.info("🚫 Modal dismissed via fallback selector: %s", sel)
                page_obj.wait_for_timeout(400)
                return
        except Exception:
            pass

    # ── 3. Escape key ─────────────────────────────────────────────────────────
    try:
        page_obj.keyboard.press("Escape")
        page_obj.wait_for_timeout(500)
        logger.info("🚫 Modal dismissed via Escape key")
    except Exception:
        pass

    # ── 4. JS force-hide (always run as safety net) ───────────────────────────
    try:
        page_obj.evaluate(
            "[document.querySelector('#login-modal'),"
            " document.querySelector('#loginPop'),"
            " document.querySelector('.jd_modal')]"
            ".forEach(el => el && (el.style.display = 'none'))"
        )
        logger.info("🚫 Modal force-hidden via JavaScript")
    except Exception:
        pass

    # ── 5. Confirm modal is gone (up to 1.5 s) ───────────────────────────────
    for selector in ["#login-modal", "#loginPop", ".jd_modal"]:
        try:
            page_obj.wait_for_selector(selector, state="hidden", timeout=1500)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────────────────────────────────────────
def search(page_obj, text: str):
    term = str(text).strip()

    # ── Dismiss any modal/overlay using stored manual locators first ────────────
    # Wait up to 6 s for JustDial's login popup to appear (it fires ~5 s after load)
    _dismiss_modal(page_obj)

    selectors = [
        "#main-auto",                        # justdial
        "#srchbx", "#main_search",
        "input[name='search']", "input[name='q']",
        "input[type='search']",
        "input.search-input",
        "input[placeholder*='Search' i]",    # case-insensitive
        "input[placeholder*='search' i]",
    ]
    search_box = None

    for selector in selectors:
        try:
            el = page_obj.locator(selector).first
            if el.is_visible(timeout=3000):
                search_box = el
                logger.info("🎯 Search input found: %s", selector)
                break
        except Exception:
            continue

    if not search_box:
        raise Exception("Search input not found")

    # ── Safety net: force-hide any blocking modal before clicking ─────────────
    try:
        page_obj.evaluate(
            "[document.querySelector('#login-modal'),"
            " document.querySelector('#loginPop'),"
            " document.querySelector('.jd_modal')]"
            ".forEach(el => el && (el.style.display = 'none'))"
        )
    except Exception:
        pass

    search_box.click()
    search_box.press("Control+A")
    search_box.press("Delete")
    search_box.type(term, delay=80)

    try:
        suggestion = page_obj.locator("li").filter(
            has_text=re.compile(term, re.IGNORECASE)
        ).first
        if suggestion.is_visible(timeout=1500):
            suggestion.click()
            logger.info("Selected suggestion")
        else:
            search_box.press("Enter")
    except Exception:
        search_box.press("Enter")

    logger.info("⏳ Waiting for UI state transition...")
    _stabilize_page(page_obj)

    try:
        page_obj.wait_for_selector(
            "div.resultbox_info, .resultbox, .jd_search_result", timeout=5000
        )
        logger.info("Business listings detected")
    except PlaywrightTimeoutError:
        pass

    return True


# ─────────────────────────────────────────────────────────────────────────────
# DATA STORE / TYPE CASTING
# ─────────────────────────────────────────────────────────────────────────────
def store_specific_data_type(raw_value, data_type, variable_name):
    val = resolve_variables(str(raw_value))
    try:
        if data_type in ("integer", "decimal"):
            clean_text = val.replace(',', '')
            matches = re.findall(r'-?\d+\.?\d*', clean_text)
            if not matches:
                raise ValueError(f"No numeric values found in '{val}'")
            extracted_num = float(matches[0])
            RUNTIME_VARIABLES[variable_name] = int(extracted_num) if data_type == "integer" else extracted_num
        elif data_type == "alphanumeric":
            RUNTIME_VARIABLES[variable_name] = re.sub(r'[^a-zA-Z0-9]', '', val)
        elif data_type == "boolean":
            RUNTIME_VARIABLES[variable_name] = val.strip().lower() in ('true', 'yes', '1', 'y', 't')
        elif data_type == "list":
            RUNTIME_VARIABLES[variable_name] = [x.strip() for x in val.split(',') if x.strip()]
        else:
            RUNTIME_VARIABLES[variable_name] = str(val)
        logger.info(
            f"💾 DATA CASTING: Saved {data_type.upper()} -> '{RUNTIME_VARIABLES[variable_name]}' "
            f"(Stored as '${variable_name}')"
        )
    except ValueError as e:
        raise Exception(f"❌ Type Casting Error: Could not convert '{val}' into {data_type}. {e}")


# ─────────────────────────────────────────────────────────────────────────────
# STRING MANIPULATION & MATH
# ─────────────────────────────────────────────────────────────────────────────
def replace_special_chars(source_text, chars_to_remove, target_variable):
    pattern = f"[{re.escape(chars_to_remove)}]"
    cleaned_text = re.sub(pattern, "", str(source_text))
    normalized_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    RUNTIME_VARIABLES[target_variable] = normalized_text
    logger.info(f"💾 Cleaned Text: '{normalized_text}' -> Stored as '${target_variable}'")


def split_and_store_text(source_text, delimiter, index, target_variable):
    parts = source_text.split(delimiter)
    try:
        extracted_part = parts[int(index)].strip()
        RUNTIME_VARIABLES[target_variable] = extracted_part
        logger.info(f"💾 Split Text: '{extracted_part}' -> Stored as '${target_variable}'")
    except IndexError:
        raise Exception(
            f"Split Error: Cannot grab position {index}. "
            f"String only split into {len(parts)} parts."
        )
    except ValueError:
        raise Exception(f"Split Error: Index '{index}' must be a valid number (e.g., 0, 1, 2).")


def concatenate_text(text1, text2, target_variable):
    combined = f"{text1}{text2}"
    RUNTIME_VARIABLES[target_variable] = combined
    logger.info(f"💾 Concatenated: '{combined}' -> Stored as '${target_variable}'")


def _get_numeric_value(input_val):
    input_str = str(input_val).strip()
    if input_str in RUNTIME_VARIABLES:
        input_str = str(RUNTIME_VARIABLES[input_str])
    input_str = input_str.replace(',', '').replace('$', '').replace('₹', '').strip()
    try:
        return float(input_str)
    except ValueError:
        raise Exception(
            f"Math Parse Error: Cannot convert '{input_val}' (resolved to '{input_str}') into a valid number."
        )


def execute_math(num1, operator, num2, target_variable):
    try:
        n1 = _get_numeric_value(num1)
        n2 = _get_numeric_value(num2)
        if operator == '+':
            res = n1 + n2
        elif operator == '-':
            res = n1 - n2
        elif operator == '*':
            res = n1 * n2
        elif operator == '/':
            if n2 == 0:
                raise Exception("Cannot divide by zero.")
            res = n1 / n2
        else:
            raise Exception("Invalid operator. Use +, -, *, or /")

        if res.is_integer():
            res = int(res)
        RUNTIME_VARIABLES[target_variable] = str(res)
        logger.info(f"💾 Math Result: {n1} {operator} {n2} = '{res}' -> Stored as '${target_variable}'")
    except Exception as e:
        raise Exception(f"Math Operation Failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# VERIFICATION ENGINES
# ─────────────────────────────────────────────────────────────────────────────
def verify_global_exact_text(page, text: str, ignore_case=False, exact_match=False):
    ignore_casing = _parse_boolean(ignore_case)
    match_mode = "EXACT" if exact_match else "CONTAINS"
    logger.info(f"🔎 Verifying {match_mode} text globally: '{text}' (Ignore Case: {ignore_casing})")
    if ignore_casing:
        pattern = f"^{re.escape(str(text))}$" if exact_match else re.escape(str(text))
        loc = page.get_by_text(re.compile(pattern, re.IGNORECASE))
    else:
        loc = page.get_by_text(str(text), exact=exact_match)
    # The text must be SHOWN somewhere. `.first` alone picked the first match in
    # the DOM, often a copy in a closed menu or filter sheet, and failed while
    # the same text was plainly on screen.
    expect(loc.filter(visible=True).first).to_be_visible(timeout=5000)
    logger.info(f"Global {match_mode.lower()} match confirmed.")


def verify_element_exact_text(page, locator_name, expected_text, ignore_case=False):
    ignore_casing = _parse_boolean(ignore_case)
    logger.info(
        f"🔎 Verifying EXACT text '{expected_text}' inside '{locator_name}' "
        f"(Ignore Case: {ignore_casing})"
    )
    loc = _get_healed_element_locator(page, locator_name)
    expect(loc).to_have_text(str(expected_text), ignore_case=ignore_casing, timeout=5000)
    logger.info("Element exact match confirmed.")


def verify_element_contains_text(page, locator_name, partial_text, ignore_case=False):
    ignore_casing = _parse_boolean(ignore_case)
    logger.info(
        f"🔎 Verifying partial text '{partial_text}' inside '{locator_name}' "
        f"(Ignore Case: {ignore_casing})"
    )
    loc = _get_healed_element_locator(page, locator_name)
    expect(loc).to_contain_text(str(partial_text), ignore_case=ignore_casing, timeout=5000)
    logger.info("Element partial match confirmed.")


def verify_multiple_global_texts(page, comma_separated_texts: str, ignore_case=False):
    ignore_casing = _parse_boolean(ignore_case)
    text_list = [t.strip() for t in str(comma_separated_texts).split(',')]
    logger.info(f"🔎 Verifying MULTIPLE texts globally: {text_list} (Ignore Case: {ignore_casing})")
    for text in text_list:
        if not text:
            continue
        try:
            if ignore_casing:
                loc = page.get_by_text(re.compile(re.escape(text), re.IGNORECASE)).first
            else:
                loc = page.get_by_text(text, exact=False).first
            expect(loc).to_be_visible(timeout=5000)
            logger.info(f"Found: '{text}'")
        except AssertionError:
            raise Exception(f"Verification Failed: Could not find '{text}' on the page.")


def verify_string_variable_contains(source_text, expected_match, ignore_case=False):
    ignore_casing = _parse_boolean(ignore_case)
    logger.info(f"🔎 Verifying variable contains: '{expected_match}' (Ignore Case: {ignore_casing})")
    src = str(source_text).lower() if ignore_casing else str(source_text)
    match = str(expected_match).lower() if ignore_casing else str(expected_match)
    if not match.strip():
        raise Exception("❌ The expected text is empty (a ${variable} probably resolved to '') — "
                        "an empty 'contains' would pass on anything.")
    if match not in src:
        raise Exception(f"❌ Match Failed: Could not find '{expected_match}' in variable '{source_text}'")
    logger.info("✅ Variable Text Match Success.")


def verify_stored_variable_contains(variable_name, partial_text, ignore_case=False):
    if variable_name not in RUNTIME_VARIABLES:
        raise Exception(
            f"❌ Execution Error: Variable '{variable_name}' is not stored in memory. "
            f"Did you run the extraction step first?"
        )
    stored_text = str(RUNTIME_VARIABLES[variable_name])
    ignore_casing = _parse_boolean(ignore_case)
    logger.info(
        f"🔎 Verifying stored variable '{variable_name}' contains: '{partial_text}' "
        f"(Ignore Case: {ignore_casing})"
    )
    src = stored_text.lower() if ignore_casing else stored_text
    match_text = str(partial_text).lower() if ignore_casing else str(partial_text)
    if not match_text.strip():
        raise Exception(
            f"❌ The expected text for '{variable_name}' is empty (a ${{variable}} probably resolved "
            f"to '') — an empty 'contains' would pass on anything.")
    if match_text not in src:
        raise Exception(
            f"❌ Match Failed: Could not find '{partial_text}' anywhere inside stored variable "
            f"'{variable_name}' (Current Value: '{stored_text}')"
        )
    logger.info("✅ Stored Variable Partial Match Success.")


# ─────────────────────────────────────────────────────────────────────────────
# WAITS & SCROLLS
# ─────────────────────────────────────────────────────────────────────────────
#: Result-page markers for EVERY platform. Only the desktop container was
#: listed, so on mobilesite this step waited out its full 15 s on every call
#: (14 calls = 3.5 min per Recommended run) and then passed anyway.
_RESULT_PAGE_MARKERS = ", ".join([
    ".result-content-container",           # website
    "h1[class*='result--h1']",             # mobilesite (Waptouch) results heading
    "[class*='resultlist--']",             # mobilesite result cards
    "[class*='resultbox']",                # older desktop / JDMart listings
])


_RESULTS_URL = re.compile(r"/nct-\d+|[?&/]search", re.I)


def wait_for_result_page_load(page):
    """
    Wait until a search-results page has rendered.

    On a results URL (…/nct-<id>) the step FAILS when no results markup shows
    up — it used to log a warning and pass, so an error page, captcha or login
    wall went unnoticed and the negative checks after it passed on a broken
    page. Flows that also use this step as a plain "wait for the page" on the
    home page keep working: off a results URL it just waits for the page to load.
    RESULT_PAGE_SOFT=1 restores the old never-fail behaviour.
    """
    started = time.perf_counter()
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    if not _RESULTS_URL.search(url):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:  # noqa: BLE001 — busy pages never go idle; loaded is enough
            pass
        logger.info("✅ Page loaded (not a results URL) in %.1fs.", time.perf_counter() - started)
        return
    try:
        page.wait_for_selector(_RESULT_PAGE_MARKERS, state="attached",
                               timeout=int(os.getenv("RESULT_PAGE_TIMEOUT_MS", "12000")))
        logger.info("✅ Result page loaded in %.1fs.", time.perf_counter() - started)
    except Exception:
        try:
            where = f"{_strip_credentials(page.url)} — title {page.title()!r}"
        except Exception:  # noqa: BLE001
            where = "(page not readable)"
        msg = (f"❌ No search results rendered after {time.perf_counter() - started:.0f}s on {where}. "
               "Check for an error page, captcha or login wall.")
        if os.getenv("RESULT_PAGE_SOFT") == "1":
            logger.warning(msg)
            return
        raise AssertionError(msg)


def wait_seconds(page, seconds: float):
    secs = float(seconds)
    if secs > settings.MAX_WAIT_S:
        raise ValueError(f"wait {seconds:g} seconds is above the limit of {settings.MAX_WAIT_S} s "
                         f"— use 'wait until element … is visible' for long waits, or raise MAX_WAIT_S.")
    page.wait_for_timeout(secs * 1000)


def wait_page_load(page):
    """Wait until the page has finished loading (the load event), then briefly for
    network quiet — Testsigma's "Wait until the current page is loaded completely"."""
    started = time.perf_counter()
    page.wait_for_load_state("load", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:  # noqa: BLE001 — pages with trackers never go idle; loaded is enough
        pass
    logger.info("✅ Page loaded in %.1fs", time.perf_counter() - started)


def refresh_page(page):
    page.reload(wait_until="load")


def scroll_to_element(page, target: str) -> None:
    """Scroll until the named element is visible in the viewport."""
    from locators.manager import (get_alternate_selectors, get_locator_and_dna,
                              promote_selector)
    xpath, _ = get_locator_and_dna(target)
    if not xpath:
        raise Exception(f"Locator '{target}' not found for scroll_to")
    _get_locator_root(page).locator(xpath).first.scroll_into_view_if_needed(timeout=8000)
    logger.info("📜 Scrolled to element: %s", target)


def vertical_scroll(page_obj, amount=500):
    page_obj.mouse.wheel(0, int(amount))
    logger.info("📜 Scrolled down by %s pixels", amount)


def scroll_until_text_visible(page, text, max_scrolls=None, scroll_wait=2):
    if max_scrolls is None:
        max_scrolls = get_default_scroll_count()
    scrolls = 0
    target_text = str(text).strip('"').strip("'")

    while scrolls < int(max_scrolls):
        locator = page.get_by_text(target_text, exact=True)
        if locator.count() > 0 and locator.first.is_visible(timeout=500):
            return True
        page.mouse.wheel(0, 500)
        scrolls += 1
        if scroll_wait:
            page.wait_for_timeout(float(scroll_wait) * 1000)   # seconds -> ms (was 1500: "wait 1" slept 1.5 s)
    # Scrolling to the limit without ever seeing the text used to "pass" — the
    # step then only cost time and hid the fact that the text was not there.
    raise Exception(f"❌ Text '{target_text}' not visible after {max_scrolls} scroll(s).")


def save_page_source(page, name: str) -> str:
    """Write the current DOM to data/logs/pagesource_<name>.html (debugging aid)."""
    import re as _re
    from config import settings
    safe = _re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "page").strip("_") or "page"
    path = os.path.join(settings.LOGS_DIR, f"pagesource_{safe}.html")
    os.makedirs(settings.LOGS_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.content())
    logger.info("💾 Page source saved: %s", path)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# SWIPE — a finger dragged across the screen, as on a phone
# ─────────────────────────────────────────────────────────────────────────────
#: Where the finger starts and ends, as a share of the screen height.
_SWIPE_SPAN = {
    "bottom_top": (0.80, 0.20), "top_bottom": (0.20, 0.80),
    "bottom_middle": (0.80, 0.50), "middle_top": (0.50, 0.20),
    "top_middle": (0.20, 0.50), "middle_bottom": (0.50, 0.80),
}
#: Horizontal swipes (carousels, galleries): fractions of the viewport WIDTH.
_SWIPE_SPAN_X = {"right_left": (0.85, 0.15), "left_right": (0.15, 0.85)}


def swipe_screen(page, span: str = "bottom_top", duration_s: float | None = None) -> None:
    """
    Drag a finger across the screen — Testsigma's "Swipe bottom to top".

    `scroll down N` sends mouse-wheel events and jumps the page; a site that
    reacts to touch (popups that open "when the user scrolls", lazy sections,
    sticky bars) may not see it as a person scrolling. This is a real touch
    gesture through the browser's own input pipeline (touch start, moves, end,
    then the page's own momentum), so the page receives what a phone sends.
    On a desktop page (no touch) it falls back to the same gesture with a mouse.
    """
    size = page.viewport_size or page.evaluate("() => ({width: innerWidth, height: innerHeight})")
    w, h = int(size["width"]), int(size["height"])
    secs = float(duration_s) if duration_s else 0.35
    touch = bool(page.evaluate("() => navigator.maxTouchPoints > 0 || 'ontouchstart' in window"))
    # Real touch events go through CDP, which only Chromium has. On WebKit
    # (iPhone Safari) and Firefox the gesture is a pointer drag instead — what
    # a carousel's "simulateTouch" listens for — so the step works everywhere.
    try:
        engine = page.context.browser.browser_type.name
    except Exception:  # noqa: BLE001
        engine = "chromium"
    if engine != "chromium":
        touch = False
    horizontal = span in _SWIPE_SPAN_X
    if horizontal:
        x0, x1 = _SWIPE_SPAN_X[span]
        xa, xb, ya, yb = int(x0 * w), int(x1 * w), int(h / 2), int(h / 2)
    else:
        y0, y1 = _SWIPE_SPAN.get(span, _SWIPE_SPAN["bottom_top"])
        xa, xb, ya, yb = int(w / 2), int(w / 2), int(y0 * h), int(y1 * h)
    steps = max(8, min(120, int(secs / 0.016)))
    if touch:
        # touchstart -> touchmoves -> touchend, as a finger does; the browser
        # then scrolls the page itself, momentum included.
        cdp = page.context.new_cdp_session(page)
        try:
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": xa, "y": ya}]})
            for i in range(1, steps + 1):
                cdp.send("Input.dispatchTouchEvent", {
                    "type": "touchMove", "touchPoints": [{"x": xa + (xb - xa) * i / steps,
                                                          "y": ya + (yb - ya) * i / steps}]})
                page.wait_for_timeout(int(secs * 1000 / steps))
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        finally:
            try:
                cdp.detach()
            except Exception:  # noqa: BLE001
                pass
    elif horizontal or engine != "chromium":
        # Pointer drag: a carousel listens for it, and on a mobile WebKit page
        # a vertical drag scrolls like a finger would.
        page.mouse.move(xa, ya)
        page.mouse.down()
        for i in range(1, steps + 1):
            page.mouse.move(xa + (xb - xa) * i / steps, ya + (yb - ya) * i / steps, steps=1)
            page.wait_for_timeout(int(secs * 1000 / steps))
        page.mouse.up()
        if not horizontal:
            # A drag on a plain page does not scroll it; add the wheel movement.
            page.mouse.wheel(0, ya - yb)
    else:
        # Desktop page: the same movement as small wheel steps.
        for _ in range(steps):
            page.mouse.wheel(0, (ya - yb) / steps)
            page.wait_for_timeout(int(secs * 1000 / steps))
    page.wait_for_timeout(500)               # let the fling settle
    logger.info("👆 Swiped %s (%s, %.1fs)", span.replace("_", " to "), "touch" if touch else "mouse", secs)


def swipe_until_element_visible(page, target: str, span: str = "bottom_top",
                                max_swipes: int = 15, wait_s: float = 1,
                                closers: list[str] | None = None) -> None:
    """Swipe, look, swipe again — Testsigma's "While X is not visible: swipe".
    `closers`: popups that may open on the way (e.g. a location sheet); each is
    closed when it shows, so it does not block the next swipe."""
    from locators.manager import get_locator_and_dna
    xpath, _ = get_locator_and_dna(target)
    if not xpath:
        raise Exception(f"Locator '{target}' not found")
    close_xpaths = []
    for c in closers or []:
        cx, _ = get_locator_and_dna(c)
        if not cx:
            raise Exception(f"Locator '{c}' not found")
        close_xpaths.append((c, cx))

    # One page.evaluate answers "which closers are showing?" for ALL of them —
    # a single browser round-trip per swipe however many popups are listed,
    # instead of two locator calls per popup. Only a popup that is actually
    # on screen costs anything more (the click).
    _WHICH_VISIBLE = """(xps) => xps.map((xp, i) => {
        try {
            const r = document.evaluate(xp, document, null,
                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (!r || !(r instanceof Element)) return -1;
            const cs = getComputedStyle(r);
            if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return -1;
            const b = r.getBoundingClientRect();
            return (b.width > 0 && b.height > 0 && b.bottom > 0 && b.top < innerHeight) ? i : -1;
        } catch (e) { return -1; }
    }).filter(i => i >= 0)"""

    def close_popups() -> None:
        if not close_xpaths:
            return
        try:
            showing = page.evaluate(_WHICH_VISIBLE, [cx for _, cx in close_xpaths]) or []
        except Exception:  # noqa: BLE001 — mid-navigation; try again next swipe
            return
        for i in showing:
            name, cx = close_xpaths[i]
            try:
                page.locator(cx).first.click(timeout=2000)
                logger.info("✖️ Closed '%s' while swiping", name)
                page.wait_for_timeout(300)
            except Exception:  # noqa: BLE001 — it went away on its own
                pass
    size = page.viewport_size or {"width": 412, "height": 915}

    def seen() -> bool:
        loc = page.locator(xpath).first
        try:
            if loc.count() == 0 or not loc.is_visible(timeout=300):
                return False
            box = loc.bounding_box(timeout=300)
        except Exception:  # noqa: BLE001
            return False
        return bool(box) and box["y"] < size["height"] and box["y"] + box["height"] > 0

    for i in range(int(max_swipes) + 1):
        if seen():
            logger.info("👆 '%s' in view after %d swipe(s)", target, i)
            return
        if i == int(max_swipes):
            break
        close_popups()
        swipe_screen(page, span)
        if wait_s:
            page.wait_for_timeout(int(float(wait_s) * 1000))
    raise Exception(f"'{target}' did not appear after {max_swipes} swipe(s) {span.replace('_', ' to ')}.")


def scroll_until_element_visible(page, target: str, pixels=500, direction="down",
                                 max_scrolls=None, scroll_wait=1) -> bool:
    """
    Scroll by a fixed number of pixels until the named element is inside the
    viewport, then stop. Raises if it never appears, so the step fails loudly
    instead of the next click failing with a less useful message.

    "Visible" here means INSIDE THE VIEWPORT, not merely rendered: Playwright's
    is_visible() is true for an element three screens below the fold, which is
    exactly the case this command exists to handle.
    """
    from locators.manager import get_locator_and_dna
    if max_scrolls is None:
        max_scrolls = get_default_scroll_count()
    xpath, _ = get_locator_and_dna(target)
    if not xpath:
        raise Exception(f"Locator '{target}' not found for scroll_until_element_visible")
    px = int(pixels or 500)
    dx, dy = {"down": (0, px), "up": (0, -px), "right": (px, 0), "left": (-px, 0)}.get(
        str(direction or "down").lower(), (0, px))
    viewport = page.viewport_size or {"width": 1280, "height": 720}

    def _in_viewport() -> bool:
        loc = page.locator(xpath).first
        try:
            if loc.count() == 0 or not loc.is_visible(timeout=300):
                return False
            box = loc.bounding_box(timeout=300)
        except Exception:  # noqa: BLE001 — detached / not yet rendered
            return False
        if not box:
            return False
        return (0 <= box["y"] < viewport["height"] - 1 and box["y"] + box["height"] > 0
                and 0 <= box["x"] < viewport["width"] - 1 and box["x"] + box["width"] > 0)

    for i in range(int(max_scrolls) + 1):
        if _in_viewport():
            logger.info("📜 '%s' is in view after %d scroll(s) of %dpx %s", target, i, px, direction)
            return True
        if i == int(max_scrolls):
            break
        page.mouse.wheel(dx, dy)
        if scroll_wait:
            page.wait_for_timeout(float(scroll_wait) * 1000)
    raise Exception(
        f"'{target}' did not come into view after {max_scrolls} scroll(s) of {px}px {direction} "
        f"(locator: {xpath}). Increase 'scroll count', change 'scroll by', or check the locator.")


#: Locator names whose content is blanked out of every screenshot.
#: Empty by default — screenshots capture the page as-is. Populate this (e.g. with
#: "mobile_number_input", "otp_input", "otp_sent_number") when runs use real customer
#: data or when evidence leaves the local machine.
SENSITIVE_SCREENSHOT_LOCATORS: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER ALERTS
#
# An alert is drawn by the browser, so no locator can reach it and an unhandled
# one stalls the run until it times out. Playwright answers a dialog through a
# handler registered BEFORE the action that triggers it, so each of these arms a
# one-shot handler and records what the dialog said for the verify variants.
# ─────────────────────────────────────────────────────────────────────────────

_LAST_DIALOG: dict = {"message": "", "type": "", "seen": False}


def _arm_dialog(page, action: str, reply: str = "") -> None:
    """Answer the next dialog once, remembering its text."""
    def _handle(dialog):
        _LAST_DIALOG.update({"message": dialog.message, "type": dialog.type,
                             "seen": True})
        try:
            if action == "accept":
                dialog.accept(reply) if reply else dialog.accept()
            else:
                dialog.dismiss()
        except Exception:  # noqa: BLE001 — a dialog closed by the page itself
            pass
    page.once("dialog", _handle)


def accept_alert(page, reply: str = "") -> None:
    _arm_dialog(page, "accept", reply)
    _settle_dialog(page)
    logger.info("✅ Accepted browser alert%s", f" with '{reply}'" if reply else "")


def dismiss_alert(page) -> None:
    _arm_dialog(page, "dismiss")
    _settle_dialog(page)
    logger.info("✅ Dismissed browser alert")


def type_into_alert(page, text: str) -> None:
    """Answer a prompt() dialog with `text`."""
    accept_alert(page, reply=text)


def _settle_dialog(page, timeout_ms: int = 1500) -> None:
    """Give an already-open dialog a moment to reach the handler."""
    try:
        page.wait_for_timeout(min(timeout_ms, 1500))
    except Exception:  # noqa: BLE001
        pass


def verify_alert_present(page) -> None:
    if not _LAST_DIALOG.get("seen"):
        _settle_dialog(page)
    if not _LAST_DIALOG.get("seen"):
        raise AssertionError(
            "No browser alert appeared. If the alert is triggered by a step, put "
            "`accept alert` or `dismiss alert` AFTER that step — the handler is "
            "armed for the next dialog.")
    logger.info("✅ Alert was present: %r", _LAST_DIALOG.get("message", ""))


def verify_alert_text(page, expected: str) -> None:
    verify_alert_present(page)
    actual = _LAST_DIALOG.get("message", "")
    if expected.strip().lower() not in actual.strip().lower():
        raise AssertionError(f"Alert said {actual!r}, expected it to contain {expected!r}")
    logger.info("✅ Alert text matched: %r", actual)


# ─────────────────────────────────────────────────────────────────────────────
# COOKIES
# ─────────────────────────────────────────────────────────────────────────────

def delete_all_cookies(page) -> None:
    """Cookies AND the open site's local / session storage.
    With only the cookies gone, justdial's page script still found its saved
    session in storage and the next page came up blank (or redirected in a
    loop after sign-in). Clearing both is a clean start, as a new visitor."""
    page.context.clear_cookies()
    try:
        page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
    except PlaywrightError:
        pass  # about:blank or a closed page — nothing stored
    logger.info("🍪 Cleared all cookies and this site's stored data")


def delete_cookie(page, name: str) -> None:
    """Remove one cookie by re-adding every other cookie after a clear."""
    ctx = page.context
    keep = [c for c in ctx.cookies() if c.get("name") != name]
    ctx.clear_cookies()
    if keep:
        ctx.add_cookies(keep)
    logger.info("🍪 Deleted cookie %r", name)


def verify_cookie(page, name: str) -> None:
    if not any(c.get("name") == name for c in page.context.cookies()):
        present = ", ".join(sorted(c.get("name", "") for c in page.context.cookies()))
        raise AssertionError(f"Cookie {name!r} is not set. Present: {present or '(none)'}")
    logger.info("✅ Cookie %r is set", name)


# ─────────────────────────────────────────────────────────────────────────────
# FILE UPLOAD / WINDOWS / FRAMES
# ─────────────────────────────────────────────────────────────────────────────

def upload_file(page, locator_name: str, file_path: str) -> None:
    import os as _os

    if not _os.path.exists(file_path):
        raise ValueError(f"No file at {file_path!r} to upload.")
    xpath, _dna = _resolve_live(page, locator_name)
    _get_locator_root(page).locator(xpath).first.set_input_files(file_path)
    logger.info("📎 Uploaded %s via %s", file_path, locator_name)


def switch_window_title(page, title: str) -> None:
    """Bring the window whose title contains `title` to the front."""
    for candidate in page.context.pages:
        try:
            if title.strip().lower() in (candidate.title() or "").lower():
                candidate.bring_to_front()
                # The next steps must run on this window — bring_to_front
                # alone left every later click on the old tab.
                _TEST_SESSION.active_page = candidate
                _TEST_SESSION.active_frame = None
                logger.info("🪟 Switched to window %r", candidate.title())
                return candidate
        except Exception:  # noqa: BLE001 — a page can close mid-iteration
            continue
    titles = []
    for c in page.context.pages:
        try:
            titles.append(c.title())
        except Exception:  # noqa: BLE001
            pass
    raise AssertionError(f"No open window titled like {title!r}. Open: {titles}")


def parent_frame(page) -> None:
    """Leave the current iframe for the one containing it."""
    # The live frame state is _TEST_SESSION.active_frame (the old module
    # attribute was never read, so this step did nothing).
    _TEST_SESSION.active_frame = None
    logger.info("🖼️  Returned to the parent frame")


# ─────────────────────────────────────────────────────────────────────────────
# CONDITIONAL ACTIONS — "do this only if it is there"
#
# These existed on the Appium runner but not on web, so a step that ran fine on
# Android failed to parse into anything dispatchable on the website. The case
# they exist for is the same on both: a cookie banner, a login popup or an
# interstitial that appears SOMETIMES. Without them a test either fails when the
# popup is absent, or fails when it is present.
#
# A skipped step is logged, never silent — "it did nothing and said nothing" is
# indistinguishable from a broken locator when a test later fails downstream.
# ─────────────────────────────────────────────────────────────────────────────

def _unknown_element(locator_name: str, err: Exception) -> None:
    """A name that is not a saved element is a typo, not 'absent' — fail on it.
    (A misspelt popup name used to be skipped on every run, so the popup was
    never dismissed and nothing said why.)"""
    if "not found in any page" in str(err):
        raise Exception(f"'{locator_name}' is not a saved element — check the spelling, or add "
                        f"it under Elements.") from err


def _visible_within(page, locator_name: str, timeout_s: float = 0):
    """The element's locator if it becomes visible in time, else None."""
    try:
        xpath, _dna = _resolve_live(page, locator_name)
    except Exception as e:  # noqa: BLE001
        _unknown_element(locator_name, e)
        logger.info("ℹ️  '%s' could not be resolved — treating as absent (%s)", locator_name, e)
        return None
    loc = _get_locator_root(page).locator(xpath).first
    try:
        loc.wait_for(state="visible", timeout=int(max(timeout_s, 0.5) * 1000))
        return loc
    except Exception:  # noqa: BLE001 — not appearing is the expected outcome
        return None


def click_if_visible(page, locator_name: str, timeout_s: float = 0) -> None:
    loc = _visible_within(page, locator_name, timeout_s)
    if loc is None:
        logger.info("ℹ️  click_if_visible: '%s' not visible — skipped", locator_name)
        return
    try:
        loc.click()
        logger.info("✅ click_if_visible: clicked '%s'", locator_name)
    except Exception as e:  # noqa: BLE001
        # It was visible a moment ago and is not clickable now — an overlay
        # closing, usually. Worth saying, not worth failing.
        logger.info("ℹ️  click_if_visible: '%s' appeared but the click failed (%s)",
                    locator_name, e)


def fill_if_visible(page, locator_name: str, text: str, timeout_s: float = 0) -> None:
    loc = _visible_within(page, locator_name, timeout_s)
    if loc is None:
        logger.info("ℹ️  fill_if_visible: '%s' not visible — skipped", locator_name)
        return
    try:
        loc.fill(text)
        logger.info("✅ fill_if_visible: filled '%s'", locator_name)
    except Exception as e:  # noqa: BLE001
        logger.info("ℹ️  fill_if_visible: '%s' appeared but the fill failed (%s)",
                    locator_name, e)


def verify_if_visible(page, locator_name: str, timeout_s: float = 0) -> None:
    """Passes when the element is absent — it asserts nothing about presence."""
    loc = _visible_within(page, locator_name, timeout_s)
    if loc is None:
        logger.info("ℹ️  verify_if_visible: '%s' not present — nothing to check",
                    locator_name)
        return
    logger.info("✅ verify_if_visible: '%s' is present", locator_name)


def click_if_exists(page, locator_name: str) -> None:
    """
    Clicks when the element is in the DOM, visible or not.

    Distinct from click_if_visible on purpose: a menu item inside a collapsed
    accordion exists but is not visible, and the two cases want different
    answers.
    """
    try:
        xpath, _dna = _resolve_live(page, locator_name)
    except Exception as e:  # noqa: BLE001
        _unknown_element(locator_name, e)
        logger.info("ℹ️  click_if_exists: '%s' could not be resolved — skipped", locator_name)
        return
    loc = _get_locator_root(page).locator(xpath).first
    try:
        if loc.count() == 0:
            logger.info("ℹ️  click_if_exists: '%s' not in the DOM — skipped", locator_name)
            return
        loc.click()
        logger.info("✅ click_if_exists: clicked '%s'", locator_name)
    except Exception as e:  # noqa: BLE001
        logger.info("ℹ️  click_if_exists: '%s' exists but the click failed (%s)",
                    locator_name, e)


# ─────────────────────────────────────────────────────────────────────────────
# TEXT MATCHING
#
# "contains" and "exact" were the only two shapes available, which forces an
# exact assertion on text that is only partly stable — an order id, a price with
# a varying amount, a message whose wording changes. Asserting the whole string
# then breaks on every unrelated copy change, so people stop asserting at all.
#
# Page-level matchers read the rendered body text once and compare in Python,
# rather than asking the browser for a locator: "does the page end with X" is a
# question about the whole document, not about an element.
# ─────────────────────────────────────────────────────────────────────────────

def _page_text(page) -> str:
    """The visible text of the page, whitespace-collapsed for comparison."""
    try:
        raw = page.inner_text("body")
    except Exception:  # noqa: BLE001 — a page mid-navigation has no body yet
        page.wait_for_load_state("domcontentloaded")
        raw = page.inner_text("body")
    return re.sub(r"\s+", " ", raw or "").strip()


def _element_text(page, locator_name: str) -> str:
    xpath, _dna = _resolve_live(page, locator_name)
    raw = _get_locator_root(page).locator(xpath).first.inner_text()
    return re.sub(r"\s+", " ", raw or "").strip()


def _report(kind: str, where: str, expected: str, actual: str, ok: bool) -> None:
    if ok:
        logger.info("✅ %s %s %r", where, kind, expected)
        return
    # The actual text is quoted and trimmed: a failure that does not show what
    # WAS there sends you to re-run it by hand just to find out.
    shown = actual if len(actual) <= 300 else actual[:300] + "…"
    raise AssertionError(
        f"{where} does not {kind} {expected!r}.\n  actual: {shown!r}")


def verify_page_contains(page, text: str) -> None:
    # Polls for up to 5 s: straight after a click the new content may not have
    # rendered yet, and one early snapshot failed a correct page.
    deadline = time.time() + 5
    body = _page_text(page)
    while str(text) not in body and time.time() < deadline:
        page.wait_for_timeout(300)
        body = _page_text(page)
    _report("contain", "The page", text, body, str(text) in body)


def verify_page_not_contains(page, text: str) -> None:
    # Let the page settle first: a single snapshot taken right after a click
    # read the OLD (or blank) page and passed before the text could appear.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(int(settings.ABSENCE_SETTLE_MS))
    body = _page_text(page)
    if str(text) in body:
        raise AssertionError(f"The page DOES contain {text!r}, and should not.")
    logger.info("✅ The page does not contain %r", text)


def verify_page_starts(page, text: str) -> None:
    body = _page_text(page)
    _report("start with", "The page", text, body, body.startswith(str(text)))


def verify_page_ends(page, text: str) -> None:
    body = _page_text(page)
    _report("end with", "The page", text, body, body.endswith(str(text)))


def verify_page_matches(page, pattern: str) -> None:
    body = _page_text(page)
    try:
        rx = re.compile(str(pattern))
    except re.error as e:
        # A bad pattern is an authoring mistake, not a test failure — say which.
        raise ValueError(f"{pattern!r} is not a valid regular expression: {e}") from e
    _report("match", "The page", pattern, body, bool(rx.search(body)))


def verify_title_contains(page, text: str) -> None:
    title = (page.title() or "").strip()
    _report("contain", "The page title", text, title, str(text) in title)


def verify_title_exact(page, text: str) -> None:
    title = (page.title() or "").strip()
    _report("equal", "The page title", text, title, title == str(text).strip())


def verify_element_starts(page, locator_name: str, text: str) -> None:
    actual = _element_text(page, locator_name)
    _report("start with", f"'{locator_name}'", text, actual,
            actual.startswith(str(text)))


def verify_element_ends(page, locator_name: str, text: str) -> None:
    actual = _element_text(page, locator_name)
    _report("end with", f"'{locator_name}'", text, actual, actual.endswith(str(text)))


def verify_element_matches(page, locator_name: str, pattern: str) -> None:
    actual = _element_text(page, locator_name)
    try:
        rx = re.compile(str(pattern))
    except re.error as e:
        raise ValueError(f"{pattern!r} is not a valid regular expression: {e}") from e
    _report("match", f"'{locator_name}'", pattern, actual, bool(rx.search(actual)))


def verify_element_not_contains(page, locator_name: str, text: str) -> None:
    actual = _element_text(page, locator_name)
    if str(text) in actual:
        raise AssertionError(
            f"'{locator_name}' DOES contain {text!r}, and should not.\n"
            f"  actual: {actual[:300]!r}")
    logger.info("✅ '%s' does not contain %r", locator_name, text)


def set_browser_permission(page, permission: str, decision: str) -> None:
    """
    Grant or refuse ONE browser permission mid-test.

    Run Center sets a policy for the whole run; this is for the test that needs
    geolocation granted and notifications refused. Playwright decides these at
    the context, and auto-dismisses any prompt for a permission that was not
    granted — so "deny" is expressed by revoking rather than by clicking a
    dialog no locator can reach.

    "once" grants the permission for the CURRENT page's origin only, which is
    what a real prompt's "Allow this time" does.
    """
    context = page.context
    perm = (permission or "").strip().lower()
    decision = (decision or "allow").strip().lower()
    try:
        if decision == "deny":
            context.clear_permissions()
            logger.info("🔒 Refused browser permission '%s' (all grants cleared)", perm)
            return
        origin = None
        if decision == "once":
            try:
                origin = page.url.split("/")[0] + "//" + page.url.split("/")[2]
            except Exception:  # noqa: BLE001 — about:blank and similar
                origin = None
        context.grant_permissions([perm], origin=origin) if origin \
            else context.grant_permissions([perm])
        if perm == "geolocation":
            # Granting location without a position makes every location request
            # time out, and sites show their error page instead ("Timeout
            # expired" on justdial). A real phone has a position; give one too.
            from execution.browser_manager import default_geolocation
            context.set_geolocation(default_geolocation())
        logger.info("🔓 Granted browser permission '%s'%s", perm,
                    f" for {origin}" if origin else "")
    except Exception as e:  # noqa: BLE001
        # An unknown permission name is a test-authoring mistake, not a crash.
        raise ValueError(
            f"Could not set browser permission '{perm}': {e}. Valid names include "
            f"geolocation, notifications, camera, microphone, clipboard-read.") from e


def take_screenshot(page_obj, label="capture"):
    import os
    if not settings.ENABLE_SCREENSHOTS:
        logger.info("📵 Screenshots are disabled (ENABLE_SCREENSHOTS=false). Skipping capture '%s'.", label)
        return
    _ensure_dir(settings.SCREENSHOTS_DIR)
    _fmt = "jpeg" if settings.SCREENSHOT_FORMAT in ("jpeg", "jpg") else "png"
    _ext = "jpg" if _fmt == "jpeg" else "png"
    # The label reaches a filesystem path, so it is sanitised HERE rather than
    # trusting the caller. nlp/parser already strips separators from a `take
    # screenshot as …` step, but that is one caller among several — a codeless
    # JSON step, a plan, or a future snippet taking a label would each have to
    # remember. Anything that is not a plain name becomes one.
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(label or "")).strip(".-")
    if not safe_label or not any(ch.isalnum() for ch in safe_label):
        safe_label = "capture"
    filename = os.path.join(settings.SCREENSHOTS_DIR, f"{safe_label}_{_timestamp()}.{_ext}")
    _shot_kwargs = {"type": _fmt}
    if _fmt == "jpeg":
        _shot_kwargs["quality"] = settings.SCREENSHOT_QUALITY

    # Blank any sensitive field before the image is written. Playwright paints a solid
    # box over each masked locator, so a captured mobile number or OTP can never reach
    # a PNG on disk or a report attachment.
    masks = []
    for _name in SENSITIVE_SCREENSHOT_LOCATORS:
        try:
            _sel, _ = get_locator_and_dna(_name)
            if not _sel:
                continue
            _loc = page_obj.locator(_sel)
            if _loc.count():
                masks.append(_loc)
        except Exception:
            continue
    if masks:
        logger.info("🙈 Masking %d sensitive element(s) in screenshot '%s'", len(masks), label)

    # A full-page capture has to stitch the whole scroll height, which on tall
    # lazy-loading pages can outlast the action timeout. Screenshots are diagnostic
    # output, so fall back to the viewport rather than failing the step outright.
    try:
        page_obj.screenshot(path=filename, full_page=settings.SCREENSHOT_FULL_PAGE,
                            mask=masks, timeout=settings.SCREENSHOT_TIMEOUT_MS,
                            **_shot_kwargs)
    except PlaywrightTimeoutError:
        logger.warning(
            "⏱️  Full-page screenshot '%s' timed out after %dms — capturing viewport instead.",
            label, settings.SCREENSHOT_TIMEOUT_MS,
        )
        page_obj.screenshot(path=filename, full_page=False, mask=masks,
                            timeout=settings.SCREENSHOT_TIMEOUT_MS, **_shot_kwargs)

    logger.info("📸 Screenshot Saved: %s", filename)


# ─────────────────────────────────────────────────────────────────────────────
# TAB / WINDOW MANAGEMENT (Playwright BrowserContext)
# ─────────────────────────────────────────────────────────────────────────────

#: Tabs a step can name instead of counting to. An index is only knowable if
#: you have counted what is open, and the count changes the moment a click
#: opens a popup — which is precisely when a test needs to switch.
TAB_NAMES = ("current", "parent", "child", "new", "newest", "first", "last",
             "previous")


def _tab_named(page, where: str):
    """Resolve a named tab to a Page, or raise saying why it could not be."""
    pages = page.context.pages
    active = _TEST_SESSION.active_page or page
    where = (where or "").strip().lower()

    if not pages:
        raise AssertionError("❌ There are no open tabs.")
    if where == "current":
        return active
    if where == "first":
        return pages[0]
    if where in ("last", "new", "newest"):
        return pages[-1]
    if where == "parent":
        # Playwright records which page opened which. That is the real
        # relationship — "the tab before this one" is only the same thing until
        # a third tab appears.
        parent = active.opener()
        if parent is None or parent.is_closed():
            raise AssertionError(
                "❌ This tab has no parent — nothing opened it. Use "
                "'switch to tab 0', or name the window by its title.")
        return parent
    if where == "child":
        kids = [p for p in pages
                if p is not active and not p.is_closed() and p.opener() is active]
        if not kids:
            raise AssertionError(
                f"❌ This tab has not opened any others. Open tabs: {len(pages)}. "
                f"If the click that opens one has not run yet, wait for it first.")
        return kids[-1]                      # the most recent child
    if where == "previous":
        i = pages.index(active) if active in pages else 0
        if i == 0:
            raise AssertionError("❌ This is already the first tab.")
        return pages[i - 1]
    raise AssertionError(
        f"❌ Unknown tab '{where}'. Use one of: {', '.join(TAB_NAMES)}, "
        f"or a number.")


def switch_tab(page, index=None, where: str = ""):
    """
    Focus a tab, by 0-based index or by name (parent, child, current, …).

    Both forms land here so that "which tab am I on" has exactly one answer and
    one place that sets it.
    """
    pages = page.context.pages
    if where:
        target = _tab_named(page, where)
        label = where
    else:
        index = int(index or 0)
        if index < 0 or index >= len(pages):
            raise AssertionError(
                f"❌ Tab index {index} out of range. "
                f"Open tabs: {len(pages)}  (valid: 0–{len(pages) - 1})"
            )
        target = pages[index]
        label = f"tab {index}"
    _TEST_SESSION.active_page = target
    _TEST_SESSION.active_frame = None
    _TEST_SESSION.active_page.bring_to_front()
    logger.info("🪟 Switched to %s — %s", label,
                _strip_credentials(_TEST_SESSION.active_page.url))


def close_tab(page, index=None):
    """Close a tab by index (or the current active tab when index is None)."""
    ctx = page.context
    pages = ctx.pages
    if index is not None:
        if index < 0 or index >= len(pages):
            raise AssertionError(f"❌ Tab index {index} out of range (0–{len(pages) - 1})")
        target = pages[index]
    else:
        target = _TEST_SESSION.active_page or page
    target.close()
    remaining = ctx.pages
    _TEST_SESSION.active_page = remaining[0] if remaining else None
    _TEST_SESSION.active_frame = None
    if _TEST_SESSION.active_page:
        _TEST_SESSION.active_page.bring_to_front()
    logger.info(
        "🗑️  Tab closed. Active tab: %s",
        _strip_credentials(_TEST_SESSION.active_page.url)
        if _TEST_SESSION.active_page else "—",
    )


def close_all_tabs(page):
    """Close every tab except tab 0 and reset focus to tab 0."""
    pages = page.context.pages
    for p in pages[1:]:
        p.close()
    _TEST_SESSION.active_page = pages[0] if pages else None
    _TEST_SESSION.active_frame = None
    if _TEST_SESSION.active_page:
        _TEST_SESSION.active_page.bring_to_front()
    logger.info(
        "🗑️  Closed all tabs. Active: %s",
        _strip_credentials(_TEST_SESSION.active_page.url)
        if _TEST_SESSION.active_page else "—",
    )


def open_new_tab(page):
    """Open a blank new tab and switch focus to it."""
    _TEST_SESSION.active_page = page.context.new_page()
    _TEST_SESSION.active_frame = None
    logger.info("🪟 Opened new tab (now active).")


def open_in_new_tab(page, url: str):
    """
    Open `url` in a new tab and leave that tab focused.

    Navigation goes through open_site, so a site alias, the domain rules and
    any stored auth behave exactly as they do for a plain `open` — a second
    copy of that logic would drift the first time an alias was added.

    The new tab records this one as its opener, so `switch to parent tab` gets
    you back without counting indexes.
    """
    parent = _TEST_SESSION.active_page or page
    # opener() is set by the browser only for tabs the PAGE opens, so a tab made
    # through the API has none. Opening it from a script in the parent keeps the
    # relationship real, which is what parent/child switching resolves against.
    with page.context.expect_page() as info:
        parent.evaluate("() => window.open('about:blank')")
    fresh = info.value
    _TEST_SESSION.active_page = fresh
    _TEST_SESSION.active_frame = None
    fresh.bring_to_front()
    open_site(fresh, url)
    logger.info("🪟 Opened %s in a new tab (now active).", fresh.url)


def list_tabs(page):
    """Log every open tab with its index, title, URL and parentage."""
    pages = page.context.pages
    active = _TEST_SESSION.active_page or page
    logger.info("📋 Open tabs (%d):", len(pages))
    for i, p in enumerate(pages):
        try:
            title = p.title()
        except Exception:  # noqa: BLE001 — a closing tab has no title
            title = "—"
        # Which tab opened which is what 'parent'/'child' resolve against, so a
        # failure to find one is only debuggable if the log shows the tree.
        try:
            opener = p.opener()
            parent = f" ← opened by [{pages.index(opener)}]" if opener in pages else ""
        except Exception:  # noqa: BLE001
            parent = ""
        logger.info("  [%d]%s %s  —  %s%s", i, " ←ACTIVE" if p is active else "",
                    title, _strip_credentials(p.url), parent)


# ─────────────────────────────────────────────────────────────────────────────
# IFRAME / FRAME MANAGEMENT (Playwright FrameLocator)
# ─────────────────────────────────────────────────────────────────────────────

def switch_iframe(page, selector: str):
    """Switch element-interaction context into an iframe matching the given CSS/XPath selector."""
    effective = get_active_page(page)
    _TEST_SESSION.active_frame = effective.frame_locator(selector)
    logger.info("🖼️  Entered iframe: %s", selector)


def exit_iframe(_page=None):
    """Return to the main frame (clear iframe context)."""
    _TEST_SESSION.active_frame = None
    logger.info("🖼️  Exited iframe — back to main frame.")


# ─────────────────────────────────────────────────────────────────────────────
# FAKE DATA GENERATION  (faker)
# ─────────────────────────────────────────────────────────────────────────────

def generate_fake_data(data_type: str, variable_name: str):
    """Generate fake test data using Faker and store in RUNTIME_VARIABLES."""
    from faker import Faker
    _faker = Faker()
    _MAP = {
        "name":      _faker.name,
        "first name": _faker.first_name,
        "last name":  _faker.last_name,
        "email":     _faker.email,
        "phone":     _faker.phone_number,
        "uuid":      _faker.uuid4,
        "number":    lambda: str(_faker.random_int(min=1, max=9999)),
        "address":   _faker.address,
        "city":      _faker.city,
        "country":   _faker.country,
        "company":   _faker.company,
        "password":  _faker.password,
        "username":  _faker.user_name,
        "url":       _faker.url,
        "date":      lambda: _faker.date(pattern="%d/%m/%Y"),
        "text":      _faker.sentence,
        "paragraph": _faker.paragraph,
        "postcode":  _faker.postcode,
        "credit card": _faker.credit_card_number,
    }
    fn = _MAP.get(data_type.lower())
    if not fn:
        raise ValueError(
            f"❌ Unknown fake data type: '{data_type}'. "
            f"Supported: {', '.join(_MAP.keys())}"
        )
    value = fn()
    RUNTIME_VARIABLES[variable_name] = value
    logger.info("🎲 Fake %s → ${%s} = %s", data_type, variable_name, value)


def generate_random_number(min_val, max_val, variable_name: str):
    """Store a random integer between min_val and max_val."""
    import random
    val = random.randint(int(min_val), int(max_val))
    RUNTIME_VARIABLES[variable_name] = val
    logger.info("🎲 Random number %s–%s → ${%s} = %d", min_val, max_val, variable_name, val)


def generate_random_string(length: int, variable_name: str):
    """Store a random alphanumeric string of given length."""
    import random, string
    val = ''.join(random.choices(string.ascii_letters + string.digits, k=int(length)))
    RUNTIME_VARIABLES[variable_name] = val
    logger.info("🎲 Random string len=%d → ${%s} = %s", int(length), variable_name, val)


# ─────────────────────────────────────────────────────────────────────────────
# DATE / TIME  (stdlib — no extra install required)
# ─────────────────────────────────────────────────────────────────────────────

def get_date_value(mode: str, variable_name: str, fmt: str = "%d/%m/%Y"):
    """
    mode: "today" | "timestamp" | "offset:+7" | "offset:-3"
    Stores result string into RUNTIME_VARIABLES.
    """
    from datetime import datetime, timedelta
    now = datetime.now()
    if mode == "today":
        value = now.strftime(fmt)
    elif mode in ("timestamp", "now"):
        value = now.strftime("%Y-%m-%d %H:%M:%S")
    elif mode.startswith("offset:"):
        days = int(mode.split(":")[1])
        value = (now + timedelta(days=days)).strftime(fmt)
    else:
        value = now.strftime(fmt)
    RUNTIME_VARIABLES[variable_name] = value
    logger.info("📅 Date [%s] → ${%s} = %s", mode, variable_name, value)


def format_date_value(date_str: str, out_fmt: str, variable_name: str):
    """Re-format a date string using DD/MM/YYYY-style tokens."""
    from datetime import datetime
    resolved = resolve_variables(date_str)
    _TOKENS = {"YYYY": "%Y", "YY": "%y", "MM": "%m", "DD": "%d",
               "HH": "%H", "mm": "%M", "ss": "%S"}
    py_fmt = out_fmt
    for tok, sf in _TOKENS.items():
        py_fmt = py_fmt.replace(tok, sf)
    for in_fmt in ("%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            value = datetime.strptime(resolved, in_fmt).strftime(py_fmt)
            RUNTIME_VARIABLES[variable_name] = value
            logger.info("📅 format_date '%s' → ${%s} = %s", resolved, variable_name, value)
            return
        except ValueError:
            continue
    raise ValueError(f"❌ Cannot parse date string: '{resolved}'")


# ─────────────────────────────────────────────────────────────────────────────
# HTTP / API CALLS  (requests — already installed)
# ─────────────────────────────────────────────────────────────────────────────

def api_get(url: str, variable_name: str, headers: dict | None = None):
    """HTTP GET → parse JSON response and store in RUNTIME_VARIABLES."""
    import requests
    url = resolve_variables(url)
    r = requests.get(url, headers=headers or {}, timeout=30)
    r.raise_for_status()
    try:
        value = r.json()
    except Exception:
        value = r.text
    RUNTIME_VARIABLES[variable_name] = value
    logger.info("🌐 API GET %s → %d → ${%s}", url, r.status_code, variable_name)


def api_post(url: str, body: str, variable_name: str, headers: dict | None = None):
    """HTTP POST with JSON/form body → parse response into RUNTIME_VARIABLES."""
    import requests, json
    url = resolve_variables(url)
    body_resolved = resolve_variables(body)
    try:
        body_data = json.loads(body_resolved)
        r = requests.post(url, json=body_data, headers=headers or {}, timeout=30)
    except (json.JSONDecodeError, TypeError):
        r = requests.post(url, data=body_resolved, headers=headers or {}, timeout=30)
    r.raise_for_status()
    try:
        value = r.json()
    except Exception:
        value = r.text
    RUNTIME_VARIABLES[variable_name] = value
    logger.info("🌐 API POST %s → %d → ${%s}", url, r.status_code, variable_name)


def extract_json_path(source_var: str, json_path: str, variable_name: str):
    """
    Extract a value from a stored JSON object using a dotted path.
    e.g. source_var='resp' json_path='data.users.0.email'
    """
    obj = RUNTIME_VARIABLES.get(source_var)
    if obj is None:
        raise ValueError(f"❌ Variable '${{{source_var}}}' not found in memory.")
    parts = json_path.lstrip("$.").split(".")
    result = obj
    for p in parts:
        try:
            result = result[int(p)] if isinstance(result, list) else result[p]
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"❌ JSON path '{json_path}' failed at key '{p}': {e}") from e
    RUNTIME_VARIABLES[variable_name] = result
    logger.info("📦 JSON path '%s' from ${%s} → ${%s} = %s", json_path, source_var, variable_name, result)


# ─────────────────────────────────────────────────────────────────────────────
# EXCEL / CSV  (openpyxl + stdlib csv)
# ─────────────────────────────────────────────────────────────────────────────

def read_excel_cell(file_path: str, row: int, col, variable_name: str, sheet: str | None = None):
    """
    Read one cell from an .xlsx file and store in RUNTIME_VARIABLES.
    col can be an integer (1-based) or a column letter ("A", "B", …).
    """
    import openpyxl
    fp = resolve_variables(file_path)
    wb = openpyxl.load_workbook(fp, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    if isinstance(col, str) and col.isalpha():
        value = ws[f"{col.upper()}{row}"].value
    else:
        value = ws.cell(row=int(row), column=int(col)).value
    RUNTIME_VARIABLES[variable_name] = value
    logger.info("📊 Excel [%s] row=%s col=%s → ${%s} = %s", fp, row, col, variable_name, value)


def read_excel_row(file_path: str, row: int, variable_name: str, sheet: str | None = None):
    """Read a full row from .xlsx as a list and store in RUNTIME_VARIABLES."""
    import openpyxl
    fp = resolve_variables(file_path)
    wb = openpyxl.load_workbook(fp, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    values = [cell.value for cell in ws[int(row)]]
    RUNTIME_VARIABLES[variable_name] = values
    logger.info("📊 Excel row %d from %s → ${%s} = %s", row, fp, variable_name, values)


def read_csv_cell(file_path: str, row: int, col, variable_name: str):
    """
    Read one cell from a CSV file and store in RUNTIME_VARIABLES.
    row is 1-based; col is 1-based int OR a header name string.
    """
    import csv
    fp = resolve_variables(file_path)
    with open(fp, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f) if isinstance(col, str) and not str(col).isdigit()
                      else csv.reader(f))
    if isinstance(col, str) and not str(col).isdigit():
        # col is a header name — DictReader used
        value = reader[int(row) - 1][col]
    else:
        value = reader[int(row) - 1][int(col) - 1]
    RUNTIME_VARIABLES[variable_name] = value
    logger.info("📄 CSV [%s] row=%s col=%s → ${%s} = %s", fp, row, col, variable_name, value)


# ─────────────────────────────────────────────────────────────────────────────
# CODELESS UI API (REGISTRY BINDINGS)
# ─────────────────────────────────────────────────────────────────────────────

@codeless_snippet("Open Site")
def ui_open_site(page, target_url_or_key: str):
    from config.environment_manager import get_active_env, transform_url

    target = target_url_or_key.lower().strip()
    if target.startswith("http://") or target.startswith("https://"):
        url_to_open = target
    elif target in SITES:
        url_to_open = SITES[target]
    else:
        raise ValueError(f"❌ Unknown site or invalid URL: {target}")

    # JSON/codeless flows bypass runner.py step-rewrite, so apply env transform here.
    resolved_url = transform_url(url_to_open, get_active_env())
    open_site(page, resolved_url)


@codeless_snippet("Click Element")
def ui_click_element(page, locator):
    click_element(page, locator)


@codeless_snippet("Fill Input Field")
def ui_fill_input(page, text_to_type, locator):
    fill_element(page, text_to_type, locator)


@codeless_snippet("Search for Category / Company / Product")
def ui_search(page, text_to_search):
    search(page, text_to_search)


@codeless_snippet("Wait For Element")
def ui_wait_for_element(page, locator, state):
    page.wait_for_selector(locator, state=state, timeout=get_standard_timeout_ms())


@codeless_snippet("Wait X Seconds")
def ui_wait_seconds(page, seconds):
    page.wait_for_timeout(float(seconds) * 1000)


@codeless_snippet("Refresh Page")
def ui_refresh_page(page):
    page.reload(wait_until="load")


@codeless_snippet("Scroll Down Page")
def ui_scroll_down(page):
    vertical_scroll(page, amount=600)


@codeless_snippet("Scroll Until Text Visible")
def ui_scroll_until_text(page, text_to_find):
    scroll_until_text_visible(page, text_to_find)


@codeless_snippet("Take Screenshot")
def ui_capture_screenshot(page):
    take_screenshot(page, label="manual_capture")


@codeless_snippet("Wait For Result Page Load")
def ui_wait_for_results(page):
    wait_for_result_page_load(page)


@codeless_snippet("Store Element Text")
def ui_store_element_text(page, locator, save_to_variable_name):
    extract_element_text(page, locator, save_to_variable_name)


# --- VERIFICATION API ---

@codeless_snippet("Verify Exact Text on Page")
def ui_verify_global_exact(page, text_to_verify, ignore_case_True_False="False"):
    verify_global_exact_text(page, text_to_verify, ignore_case_True_False)


@codeless_snippet("Verify Multiple Texts on Page")
def ui_verify_multiple_global(page, comma_separated_texts, ignore_case_True_False="False"):
    verify_multiple_global_texts(page, comma_separated_texts, ignore_case_True_False)


@codeless_snippet("Verify Exact Text in Element")
def ui_verify_element_exact(page, locator, exact_text_to_match, ignore_case_True_False="False"):
    verify_element_exact_text(page, locator, exact_text_to_match, ignore_case_True_False)


@codeless_snippet("Verify Partial Text in Element")
def ui_verify_element_contains(page, locator_to_fetch_from, partial_text_to_match, ignore_case_True_False="False"):
    verify_element_contains_text(page, locator_to_fetch_from, partial_text_to_match, ignore_case_True_False)


@codeless_snippet("Verify Variable Contains Text")
def ui_match_variable_contains(page, source_variable, text_to_find, ignore_case_True_False="False"):
    verify_string_variable_contains(source_variable, text_to_find, ignore_case_True_False)


@codeless_snippet("Verify Stored Variable Contains Partial Text")
def ui_verify_stored_var_partial(page, saved_variable_name, partial_text_to_find, ignore_case_True_False="False"):
    verify_stored_variable_contains(saved_variable_name, partial_text_to_find, ignore_case_True_False)


# --- DATA API ---

@codeless_snippet("Replace Special Characters")
def ui_regex_replace(page, source_text_or_variable, characters_to_remove, save_to_variable_name):
    replace_special_chars(source_text_or_variable, characters_to_remove, save_to_variable_name)


@codeless_snippet("Split String")
def ui_split_string(page, source_text_or_variable, delimiter, position_index, save_to_variable_name):
    split_and_store_text(source_text_or_variable, delimiter, position_index, save_to_variable_name)


@codeless_snippet("Concatenate Text")
def ui_concatenate(page, first_text_part, second_text_part, save_to_variable_name):
    concatenate_text(first_text_part, second_text_part, save_to_variable_name)


@codeless_snippet("Math Operation")
def ui_math(page, first_number_or_variable_name, operator_symbol, second_number_or_variable_name, save_to_variable_name):
    execute_math(first_number_or_variable_name, operator_symbol, second_number_or_variable_name, save_to_variable_name)


# --- EXTRACTION API ---

@codeless_snippet("Store Element Attribute (href, src, etc)")
def ui_store_attribute(page, locator, attribute_name_eg_href, save_to_variable_name):
    extract_element_attribute(page, locator, attribute_name_eg_href, save_to_variable_name)


@codeless_snippet("Store Input Field Value")
def ui_store_input_value(page, locator, save_to_variable_name):
    extract_input_value(page, locator, save_to_variable_name)


@codeless_snippet("Store Element Count")
def ui_store_element_count(page, locator, save_to_variable_name):
    extract_element_count(page, locator, save_to_variable_name)


@codeless_snippet("Store Current Page URL")
def ui_store_url(page, save_to_variable_name):
    extract_page_url(page, save_to_variable_name)


@codeless_snippet("Store Current Page Title")
def ui_store_title(page, save_to_variable_name):
    extract_page_title(page, save_to_variable_name)


@codeless_snippet("Create Custom Variable")
def ui_create_variable(page, value_to_store, save_to_variable_name):
    create_custom_variable(value_to_store, save_to_variable_name)


# --- DATA TYPE CASTING API ---

@codeless_snippet("Store Variable (Text / String)")
def ui_store_string(page, raw_text_or_variable, save_to_variable_name):
    store_specific_data_type(raw_text_or_variable, "string", save_to_variable_name)


@codeless_snippet("Store Variable (Integer / Whole Number)")
def ui_store_integer(page, raw_text_or_variable, save_to_variable_name):
    store_specific_data_type(raw_text_or_variable, "integer", save_to_variable_name)


@codeless_snippet("Store Variable (Decimal / Float)")
def ui_store_decimal(page, raw_text_or_variable, save_to_variable_name):
    store_specific_data_type(raw_text_or_variable, "decimal", save_to_variable_name)


@codeless_snippet("Store Variable (Alphanumeric Only)")
def ui_store_alphanumeric(page, raw_text_or_variable, save_to_variable_name):
    store_specific_data_type(raw_text_or_variable, "alphanumeric", save_to_variable_name)


@codeless_snippet("Store Variable (Boolean True/False)")
def ui_store_boolean(page, raw_text_or_variable, save_to_variable_name):
    store_specific_data_type(raw_text_or_variable, "boolean", save_to_variable_name)


@codeless_snippet("Store Variable (Comma-Separated List)")
def ui_store_list(page, raw_text_or_variable, save_to_variable_name):
    store_specific_data_type(raw_text_or_variable, "list", save_to_variable_name)


# ─────────────────────────────────────────────────────────────────────────────
# JAVASCRIPT ACTIONS
# Use when normal Playwright actions fail: shadow-DOM, React-controlled inputs,
# overlays that intercept clicks, or any element that needs direct JS access.
# ─────────────────────────────────────────────────────────────────────────────

def _js_selector_script(selector: str, var: str = "el") -> str:
    """Return the JS expression that resolves a CSS selector or XPath to an element."""
    if selector.startswith("//") or selector.startswith("(//"):
        import json as _j
        return (
            f'var {var} = document.evaluate({_j.dumps(selector)}, document, null, '
            f'XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;'
        )
    else:
        import json as _j
        return f'var {var} = document.querySelector({_j.dumps(selector)});'


def _resolve_to_selector(target: str) -> str:
    """
    Convert a locator name from the element store to its XPath/CSS string.
    Falls back to using target as a raw CSS/XPath if lookup fails.
    """
    try:
        from locators.manager import (get_alternate_selectors, get_locator_and_dna,
                              promote_selector)
        xpath, _ = get_locator_and_dna(target)
        if xpath:
            return xpath
    except Exception:
        pass
    return target  # treat target as a raw CSS selector / XPath


def js_click(page, target: str) -> None:
    """
    Click an element via JavaScript — bypasses overlays, pointer-event:none,
    and React synthetic event handlers that block normal Playwright .click().

    Usage in .flow:  js click search_button
    """
    import json as _j
    selector = _resolve_to_selector(target)
    find_js  = _js_selector_script(selector)
    script = (
        f'{find_js} '
        f'if (!el) throw new Error("JS click: element not found — {selector}"); '
        f'el.click();'
    )
    ep = _get_locator_root(page)
    try:
        ep.evaluate(script)
        logger.info("✅ JS click: %s", target)
    except Exception as e:
        raise Exception(f"JS click failed on '{target}': {e}") from e


def js_scroll_to(page, target: str) -> None:
    """
    Scroll an element into the viewport using scrollIntoView.

    Usage in .flow:  js scroll to load_more_btn
    """
    import json as _j
    selector = _resolve_to_selector(target)
    find_js  = _js_selector_script(selector)
    script = (
        f'{find_js} '
        f'if (el) el.scrollIntoView({{behavior:"smooth",block:"center"}});'
    )
    ep = _get_locator_root(page)
    try:
        ep.evaluate(script)
        ep.wait_for_timeout(300)
        logger.info("✅ JS scroll to: %s", target)
    except Exception as e:
        raise Exception(f"JS scroll to failed on '{target}': {e}") from e


def js_scroll(page, direction: str = "down", pixels: int = 300) -> None:
    """
    Scroll the window by N pixels in the given direction.
    direction: down | up | top | bottom

    Usage in .flow:
        js scroll down
        js scroll up 500
        js scroll top
        js scroll bottom
    """
    ep = _get_locator_root(page)
    d = direction.lower()
    if d == "down":
        ep.evaluate(f"window.scrollBy(0, {pixels})")
    elif d == "up":
        ep.evaluate(f"window.scrollBy(0, -{pixels})")
    elif d == "top":
        ep.evaluate("window.scrollTo(0, 0)")
    elif d == "bottom":
        ep.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    else:
        ep.evaluate(f"window.scrollBy(0, {pixels})")
    logger.info("✅ JS scroll %s %dpx", direction, pixels)


def js_type(page, text: str, target: str) -> None:
    """
    Set an input's value via JavaScript and fire React/Vue change events.
    Use when Playwright .fill() doesn't trigger the framework's state update.

    Usage in .flow:  js type "hello@email.com" into email_field
    """
    import json as _j
    selector  = _resolve_to_selector(target)
    find_js   = _js_selector_script(selector)
    text_safe = _j.dumps(text)
    # Use the native input value setter so React's synthetic onChange fires
    script = (
        f'{find_js} '
        f'if (!el) throw new Error("JS type: element not found — {selector}"); '
        f'var setter = Object.getOwnPropertyDescriptor('
        f'  window.HTMLInputElement.prototype, "value") || '
        f'  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value"); '
        f'if (setter && setter.set) setter.set.call(el, {text_safe}); '
        f'else el.value = {text_safe}; '
        f'el.dispatchEvent(new Event("input",  {{bubbles:true}})); '
        f'el.dispatchEvent(new Event("change", {{bubbles:true}}));'
    )
    ep = _get_locator_root(page)
    try:
        ep.evaluate(script)
        logger.info("✅ JS type into: %s", target)
    except Exception as e:
        raise Exception(f"JS type failed on '{target}': {e}") from e


def js_set_value(page, text: str, target: str) -> None:
    """Alias for js_type — set a field's value via JavaScript."""
    js_type(page, text, target)


def js_focus(page, target: str) -> None:
    """
    Focus an element using JavaScript — triggers focus events for custom widgets.

    Usage in .flow:  js focus phone_input
    """
    import json as _j
    selector = _resolve_to_selector(target)
    find_js  = _js_selector_script(selector)
    script   = f'{find_js} if (el) el.focus();'
    ep = _get_locator_root(page)
    try:
        ep.evaluate(script)
        logger.info("✅ JS focus: %s", target)
    except Exception as e:
        raise Exception(f"JS focus failed on '{target}': {e}") from e


def js_submit(page, target: str) -> None:
    """
    Submit a form directly via JavaScript .submit().

    Usage in .flow:  js submit login_form
    """
    import json as _j
    selector = _resolve_to_selector(target)
    find_js  = _js_selector_script(selector)
    script   = (
        f'{find_js} '
        f'if (!el) throw new Error("JS submit: element not found — {selector}"); '
        f'if (typeof el.submit === "function") el.submit(); '
        f'else el.dispatchEvent(new Event("submit", {{bubbles:true, cancelable:true}}));'
    )
    ep = _get_locator_root(page)
    try:
        ep.evaluate(script)
        logger.info("✅ JS submit: %s", target)
    except Exception as e:
        raise Exception(f"JS submit failed on '{target}': {e}") from e


def js_dispatch_event(page, event_name: str, target: str) -> None:
    """
    Dispatch a custom DOM event on an element.

    Usage in .flow:  js dispatch click on submit_btn
    """
    import json as _j
    selector   = _resolve_to_selector(target)
    find_js    = _js_selector_script(selector)
    event_safe = _j.dumps(event_name)
    script = (
        f'{find_js} '
        f'if (!el) throw new Error("JS dispatch: element not found — {selector}"); '
        f'el.dispatchEvent(new Event({event_safe}, {{bubbles:true, cancelable:true}}));'
    )
    ep = _get_locator_root(page)
    try:
        ep.evaluate(script)
        logger.info("✅ JS dispatch %s on: %s", event_name, target)
    except Exception as e:
        raise Exception(f"JS dispatch '{event_name}' failed on '{target}': {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
# VISIBILITY ASSERTIONS AND MULTI-INPUT OTP ENTRY
#
# Added for the Ask More Photos source testcases (TC_AFP_C01/C02/C03), which need
# real visibility assertions rather than a `store text` side effect, a
# condition-based wait rather than a sleep, and a six-box OTP field.
# None of these change existing fill/verify behaviour.
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_locator_or_raise(locator_name: str, page=None) -> str:
    if page is not None:
        return _resolve_live(page, locator_name)[0]
    xpath, _dna = get_locator_and_dna(locator_name)
    if not xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")
    return resolve_variables(xpath)


def verify_element_visible(page, locator_name):
    """Assert the resolved element is visible (proper Playwright assertion)."""
    selector = _resolve_locator_or_raise(locator_name, page)
    logger.info("🔎 Verifying element '%s' is visible", locator_name)
    try:
        expect(_get_locator_root(page).locator(selector).first).to_be_visible(timeout=settings.ACTION_TIMEOUT_MS)
    except AssertionError as e:
        raise Exception(
            f"Visibility assertion failed for '{locator_name}' ({selector}): {e}"
        ) from e
    logger.info("✅ Element '%s' is visible.", locator_name)


def fetch_otp_from_portal(page, mobile, variable_name, after: str = "",
                          timeout_s: int | None = None):
    """
    Read a live OTP from the QA portal and store it in a runtime variable.

    The flow never pauses and nobody is prompted — the portal is driven in its own
    browser context while the application page stays exactly where it is.

    Normal usage is a bare fetch straight after the mobile number is submitted.
    A short settle lets the SMS land first. If a stale code is somehow read, OTP
    verification fails on the page like any other assertion and is investigated
    as an ordinary failure — the optional `after` clause exists for flows that
    would rather fail at the fetch than at the assertion.

    The OTP is stored but never logged, echoed or included in an error message.
    """
    from execution.otp_portal import fetch_otp

    number = resolve_variables(str(mobile))
    previous = resolve_variables(str(after)) if after else ""
    browser = page.context.browser
    if browser is None:
        raise Exception("Cannot reach the OTP portal: no browser is attached to this page.")

    logger.info("📨 Fetching OTP from the QA portal%s",
                " (waiting for a new message)" if previous else "")
    code = fetch_otp(browser, number,
                     after=previous,
                     timeout_s=int(timeout_s or settings.OTP_PORTAL_TIMEOUT_S))
    RUNTIME_VARIABLES[variable_name] = code
    logger.info("💾 OTP stored as '$%s' (%d digits, value withheld)",
                variable_name, len(code))
    return code


def verify_element_exists(page, locator_name, timeout_ms: int | None = None):
    """
    Assert the element is PRESENT IN THE DOM (visible or not).

    The positive twin of verify_element_not_exists. Waits up to the action
    timeout for the node to be attached, so a check straight after a click
    does not fail because the page has not rendered yet.
    """
    selector = _resolve_locator_or_raise(locator_name, page)
    ms = int(timeout_ms if timeout_ms is not None else settings.ACTION_TIMEOUT_MS)
    logger.info("🔎 Verifying element '%s' exists in the DOM", locator_name)
    try:
        _get_locator_root(page).locator(selector).first.wait_for(state="attached", timeout=ms)
    except PlaywrightTimeoutError:
        raise Exception(
            f"Element '{locator_name}' ({selector}) is not present in the DOM "
            f"after {ms // 1000}s.")
    logger.info("✅ Element '%s' exists.", locator_name)


def verify_element_not_exists(page, locator_name, settle_ms: int | None = None):
    """
    Assert the element is ABSENT FROM THE DOM.

    An absence check that runs the instant the previous step finishes always
    passes — the thing it is looking for may simply not have rendered yet. So this
    waits a settle period first and only then asserts, which is why it uses a plain
    count rather than a retrying assertion (`to_have_count(0)` would succeed the
    moment the element disappeared, which is a different claim).
    """
    selector = _resolve_locator_or_raise(locator_name, page)
    settle = int(settle_ms if settle_ms is not None else settings.ABSENCE_SETTLE_MS)
    logger.info("🔎 Verifying element '%s' is absent from the DOM (settling %dms)",
                locator_name, settle)
    page.wait_for_timeout(settle)
    count = _get_locator_root(page).locator(selector).count()
    if count:
        raise Exception(
            f"Element '{locator_name}' ({selector}) should be absent from the DOM "
            f"but {count} match(es) are present."
        )
    logger.info("✅ Element '%s' is absent from the DOM.", locator_name)


def verify_element_not_visible(page, locator_name, settle_ms: int | None = None):
    """
    Assert the element is NOT VISIBLE — either absent, or present but hidden.

    The weaker of the two negative assertions, and the right one for "the success
    toaster must not appear": a toaster that exists in the DOM but is never shown
    still means no success was signalled to the user. Settles first, for the same
    reason as verify_element_not_exists.
    """
    selector = _resolve_locator_or_raise(locator_name, page)
    settle = int(settle_ms if settle_ms is not None else settings.ABSENCE_SETTLE_MS)
    logger.info("🔎 Verifying element '%s' is not visible (settling %dms)",
                locator_name, settle)
    page.wait_for_timeout(settle)
    loc = _get_locator_root(page).locator(selector)
    if loc.count() and loc.first.is_visible():
        raise Exception(
            f"Element '{locator_name}' ({selector}) is visible but should not be."
        )
    logger.info("✅ Element '%s' is not visible.", locator_name)


def wait_until_element_visible(page, locator_name, timeout_ms: int | None = None):
    """Condition-based wait for visibility — never a fixed sleep."""
    selector = _resolve_locator_or_raise(locator_name, page)
    timeout = int(timeout_ms or settings.ACTION_TIMEOUT_MS)
    logger.info("⏳ Waiting up to %dms for '%s' to become visible", timeout, locator_name)
    try:
        _get_locator_root(page).locator(selector).first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError as e:
        raise Exception(
            f"'{locator_name}' ({selector}) did not become visible within {timeout}ms"
        ) from e
    logger.info("✅ Element '%s' became visible.", locator_name)


def wait_until_element_not_visible(page, locator_name, timeout_ms: int | None = None):
    """Wait until the element is gone or hidden (a popup closing, a loader ending)."""
    selector = _resolve_locator_or_raise(locator_name, page)
    timeout = int(timeout_ms or settings.ACTION_TIMEOUT_MS)
    logger.info("⏳ Waiting up to %dms for '%s' to disappear", timeout, locator_name)
    try:
        _get_locator_root(page).locator(selector).first.wait_for(state="hidden", timeout=timeout)
    except PlaywrightTimeoutError as e:
        raise Exception(f"'{locator_name}' ({selector}) was still visible after {timeout}ms") from e
    logger.info("✅ Element '%s' is not visible.", locator_name)


def select_option(page, locator_name, option):
    """Pick an option in a <select> by its value, falling back to its visible label."""
    loc = _get_healed_element_locator(page, locator_name)
    option = str(option)
    by_value = loc.evaluate("(el, v) => [...(el.options || [])].some(o => o.value === v)", option)
    if by_value:
        loc.select_option(value=option, timeout=5000)
    else:                                   # not a value — the label people see
        loc.select_option(label=option, timeout=5000)
    logger.info("✅ Selected '%s' in '%s'", option, locator_name)


def type_into_focused(page, text):
    """Type into whatever has focus, key by key — adds to the field's current text."""
    page.keyboard.type(str(text), delay=30)
    logger.info("⌨️ Typed %d characters into the focused field", len(str(text)))


def clear_field(page, locator_name):
    """Empty an input / textarea."""
    loc = _get_healed_element_locator(page, locator_name)
    loc.fill("", timeout=5000)
    logger.info("🧹 Cleared '%s'", locator_name)


def store_javascript(page, script, variable_name):
    """Evaluate a JavaScript expression and store its result as a variable.
    Objects/arrays are stored as JSON text so later steps can verify them."""
    import json as _json
    logger.info("🧩 Storing result of JavaScript: %s", str(script)[:120])
    try:
        val = page.evaluate(f"() => ({script})")
    except Exception:
        # A statement rather than an expression: run it and take `return`.
        val = page.evaluate(f"() => {{ {script} }}")
    if isinstance(val, (dict, list)):
        val = _json.dumps(val, ensure_ascii=False)
    RUNTIME_VARIABLES[variable_name] = "" if val is None else str(val)
    # A page's resource list echoes URL-embedded logins (user:pass@host); never log those.
    import re as _re
    # Mask BEFORE truncating, and match any "user:pass@" run (URL userinfo has no
    # spaces, slashes or pipes) so a login never reaches the log in any form.
    shown = _re.sub(r"[^\s/|@:]+:[^\s/|@]+@", "***@", RUNTIME_VARIABLES[variable_name])[:2000]
    logger.info("💾 EXTRACTED (js): %s -> Stored as '$%s'", shown, variable_name)


def run_javascript(page, script):
    """Run a line of JavaScript in the page — the Testsigma 'Execute javascript' step."""
    logger.info("🧩 Running JavaScript: %s", str(script)[:120])
    page.evaluate(f"() => {{ {script} }}")


def scroll_element_horizontally(page, locator_name, pixels):
    """Scroll a horizontal carousel / tab strip sideways by N pixels."""
    loc = _get_healed_element_locator(page, locator_name)
    px = int(float(str(pixels).strip()))
    # The strip that scrolls is the element itself or its nearest scrollable parent.
    loc.evaluate("(el, px) => { let b = el; while (b && b.scrollWidth <= b.clientWidth) b = b.parentElement;"
                 " (b || el).scrollBy(px, 0); }", px)
    logger.info("↔️ Scrolled '%s' horizontally by %dpx", locator_name, px)


def remove_text(chars, source, variable_name):
    """`remove "&" from "${var}" and store as <var>` — a copy without those characters."""
    text = resolve_variables(str(source))
    out = " ".join(text.replace(str(chars), " ").split()) if str(chars).strip() else text
    RUNTIME_VARIABLES[variable_name] = out
    logger.info("💾 %r without %r -> ${%s} = %r", text[:60], chars, variable_name, out[:60])


def enter_otp(page, otp_value, locator_name):
    """
    Distribute an N-digit OTP across N ordered single-character inputs.

    The value is never logged, echoed or included in any error message — only its
    length is ever mentioned. Fails safely when the digit count and the resolved
    input count disagree, rather than partially filling the field.
    """
    selector = _resolve_locator_or_raise(locator_name, page)
    code = str(resolve_variables(str(otp_value))).strip()

    if not code.isdigit():
        raise Exception(
            f"OTP for '{locator_name}' must be digits only "
            f"(received {len(code)} characters; value withheld)."
        )

    inputs = _get_locator_root(page).locator(selector)
    count = inputs.count()
    if count == 0:
        raise Exception(f"OTP locator '{locator_name}' ({selector}) matched no inputs.")
    if count != len(code):
        raise Exception(
            f"OTP locator '{locator_name}' resolved {count} inputs but the supplied "
            f"value has {len(code)} digits — refusing to enter a partial code."
        )

    logger.info("🔐 Entering %d-digit OTP into '%s' (%d inputs). Value withheld.",
                len(code), locator_name, count)
    for i, digit in enumerate(code):
        box = inputs.nth(i)
        box.click()
        box.fill(digit)
    logger.info("✅ OTP entered across %d inputs.", count)


# ─────────────────────────────────────────────────────────────────────────────
# RECOMMENDED PRODUCTS — priority logic (GJDT-22686)
# ─────────────────────────────────────────────────────────────────────────────
#: The ticket's hierarchy, highest priority first. Bucket = (has DS, in search
#: city, has price). 360°-vs-video ordering inside a DS bucket is not checked:
#: neither the API nor the card exposes whether a product has a video.
_RP_BUCKETS = [
    (True, True, True), (True, True, False), (True, False, True), (True, False, False),
    (False, True, True), (False, True, False), (False, False, True), (False, False, False),
]
#: Cities the backend treats as ONE search city (DC logic): a Thane vendor is
#: "in city" for a Mumbai search. Keys and members are lower-case.
_RP_CITY_GROUPS = {
    "mumbai": {"mumbai", "navi mumbai", "thane", "vasai", "vasai virar", "palghar",
               "mira bhayandar", "kalyan", "dombivli", "bhiwandi"},
}

#: DS flags the last verified API response reported, keyed by product name.
#: The results page shows a badge for 360° but NOT for AI video, so a page
#: check that trusted the badge alone called a video product "no DS" and
#: failed a correct order. The API knows; the page check borrows from it.
_RP_API_DS: dict[str, bool] = {}


def _rp_same_city(item_city: str, want: str) -> bool:
    a = (item_city or "").strip().lower()
    w = (want or "").strip().lower()
    if a == w:
        return True
    for _hub, members in _RP_CITY_GROUPS.items():
        if w in members and a in members:
            return True
    return False


def _rp_has_video(x: dict) -> bool:
    """Any item-level field that names a video and is non-empty."""
    for k, v in x.items():
        if "video" in str(k).lower() and v not in (None, "", 0, "0", False, [], {}):
            return True
    return False


_RP_LABEL = {
    (True, True, True): "DS, in city, priced",   (True, True, False): "DS, in city, no price",
    (True, False, True): "DS, outside, priced",  (True, False, False): "DS, outside, no price",
    (False, True, True): "no DS, in city, priced", (False, True, False): "no DS, in city, no price",
    (False, False, True): "no DS, outside, priced", (False, False, False): "no DS, outside, no price",
}


def _rp_check_order(items: list[dict], city: str, source: str) -> None:
    """
    Assert `items` (each {name, city, ds, price}) never step UP in priority.

    Higher-priority buckets must be exhausted before a lower one appears, so
    the bucket index must be non-decreasing along the carousel. One violation
    is enough to fail; the whole table is logged so the reason is visible.
    """
    if not items:
        raise Exception(f"❌ No recommended products found in {source}.")
    want = city.strip().lower()
    rows = []
    for i, it in enumerate(items, 1):
        key = (bool(it["ds"]), _rp_same_city(it["city"], want), bool(it["price"]))
        rows.append((i, _RP_BUCKETS.index(key) + 1, _RP_LABEL[key], it["name"], it["city"] or "?"))
    logger.info("📊 Recommended products (%s) vs search city '%s':\n%s", source, city,
                "\n".join(f"   {i:>2}. P{p} [{lbl}]  {n[:50]}  ({c})" for i, p, lbl, n, c in rows))
    table = "\n".join(
        f"   {i:>2}. P{p} [{lbl}]  {n[:48]}  ({c})"
        + (f"  — {items[i - 1].get('company', '')[:28]}" if items[i - 1].get("company") else "")
        for i, p, lbl, n, c in rows)
    for prev, cur in zip(rows, rows[1:]):
        if cur[1] < prev[1]:
            raise Exception(
                f"❌ Priority order broken in {source}: #{cur[0]} '{cur[3][:50]}' is "
                f"P{cur[1]} [{cur[2]}] but comes after #{prev[0]} '{prev[3][:50]}' "
                f"which is P{prev[1]} [{prev[2]}]. A higher-priority group must be "
                f"exhausted before a lower one is shown (GJDT-22686).\n"
                f"What {source} contained at this moment (search city {city}):\n{table}")
    logger.info("✅ %s: %d products follow the priority hierarchy.", source, len(rows))


def _rp_search_city(page, explicit: str = "", api_obj=None) -> str:
    """
    The city the user searched in — never hard-coded into the step.

    Taken, in order, from: the step's own `for city "…"` (only when written),
    the API response's `results.bd_params.city` (the city the backend served
    the search for), and the page URL (`justdial.com/<City>/…`). A test that
    opens a Pune URL is therefore judged against Pune without anyone editing
    the step.
    """
    if explicit and explicit.strip():
        return resolve_variables(explicit.strip())
    if isinstance(api_obj, dict):
        bd = (api_obj.get("results") or {}).get("bd_params") if isinstance(api_obj.get("results"), dict) else None
        if isinstance(bd, dict) and bd.get("city"):
            return str(bd["city"]).strip()
    try:
        from urllib.parse import unquote, urlparse

        from execution.action_service import _TEST_SESSION
        live = (_TEST_SESSION.active_page or page) if page is not None else None
        path = urlparse(live.url).path if live is not None else ""
        first = next((seg for seg in path.split("/") if seg), "")
        if first and not first.lower().startswith(("jdmart", "nct-", "cat-")):
            return unquote(first).replace("-", " ").strip()
    except Exception:  # noqa: BLE001
        pass
    raise Exception("❌ Could not work out the search city — the API response has no "
                    "bd_params.city and the page URL has no /<City>/ segment. Add "
                    "`for city \"<City>\"` to the step.")


def verify_recommended_order_api(variable_name: str, city: str = "", page=None) -> None:
    """
    `verify recommended products order in <var>` [`for city "<city>"`]
    <var> holds the get_product_by_ncatid JSON (from `api get … as <var>`).
    The city is read from the response / page unless written in the step.
    """
    obj = RUNTIME_VARIABLES.get(variable_name)
    if obj is None:
        raise Exception(f"❌ Variable '${{{variable_name}}}' is not stored in memory.")
    city = _rp_search_city(page, city, obj)
    data = obj
    for key in ("results", "data"):
        if isinstance(data, dict) and key in data:
            data = data[key]
    if not isinstance(data, list):
        raise Exception(f"❌ '${{{variable_name}}}' does not hold results.data as a list.")
    items = []
    for x in data:
        ev = x.get("event_data") if isinstance(x, dict) else None
        ci = (ev or {}).get("contract_info") if isinstance(ev, dict) else None
        item_city = ((ci or {}).get("city") or (ci or {}).get("data_city") or "")
        if not item_city:
            m = re.search(r"/jdmart/([^/]+)/", str(x.get("url", "")))
            item_city = m.group(1).replace("-", " ") if m else ""
        # Digital Showroom = 360° (image3d / thumb_3d_image) OR AI-video /
        # showroom media (`showcase`, a non-empty list). Service categories
        # never show a badge on the results page, so this is the only signal.
        ds = (bool(x.get("image3d")) or bool(x.get("thumb_3d_image"))
              or bool(x.get("showcase")) or _rp_has_video(x))
        pm = re.search(r"pid-(\d+)", str(x.get("url", "")))
        items.append({
            "name": str(x.get("display_name1", "")),
            "city": item_city,
            "ds": ds,
            "price": bool(str(x.get("price", "")).strip()),
            "pid": pm.group(1) if pm else "",
        })
    # Keyed by PRODUCT ID, not name: two sellers can list the same product
    # name ("Business Bulk SMS Services" — Delhi with a showroom, Mumbai
    # without), and a name key let the second overwrite the first.
    _RP_API_DS.clear()
    for it in items:
        if it["pid"]:
            _RP_API_DS["pid:" + it["pid"]] = it["ds"]
        key = "nc:" + it["name"].strip().lower() + "|" + (it["city"] or "").strip().lower()
        _RP_API_DS[key] = _RP_API_DS.get(key, False) or it["ds"]
    _rp_check_order(items, city, f"API ${{{variable_name}}}")


def verify_recommended_order_page(page, city: str = "") -> None:
    """
    `verify recommended products carousel order on page` [`for city "<city>"`]
    Reads the cards as rendered: city from the card link, DS from the 360°
    badge (or from the API for AI-video products, which carry no badge on
    the results page), price from the price block.
    The search city comes from the page URL unless written in the step.
    """
    city = _rp_search_city(page, city)
    # Same reader as the city-preference check; DS for AI-video products is
    # borrowed from the last verified API response (the page has no badge).
    items = _rp_cards(page)
    try:
        _rp_check_order(items or [], city, "the page carousel")
    except Exception as e:
        shot = _rp_evidence(page, "order")
        raise Exception(f"{e}\nAll cards as rendered: {shot}" if shot else str(e)) from None


def _rp_evidence(page, tag: str) -> str:
    """
    Photograph EVERY card of the carousel, not just the 2–3 in view.

    The carousel scrolls sideways, so the step screenshot never showed the
    cards a failure was about, and the next page load may show a different
    set. The cards are copied onto a temporary white sheet as a numbered grid,
    photographed, and the sheet is removed — the page itself is not touched.
    """
    try:
        import time as _t

        from config import settings
        handle = page.evaluate_handle("""() => {
          const car = [...document.querySelectorAll('.carousel_parent_short')]
            .find(c => /Recommended (Products|Services) For You/i.test(c.textContent || ''));
          if (!car) return null;
          document.getElementById('__rp_evidence')?.remove();
          // A clean copy on a white sheet: the real carousel lives in a
          // fixed-height box, so re-flowing it in place spilled the cards
          // over the listings below and made the picture unreadable.
          const sheet = document.createElement('div');
          sheet.id = '__rp_evidence';
          sheet.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;background:#fff;'
            + 'padding:8px;width:' + Math.max(360, window.innerWidth) + 'px;box-sizing:border-box;'
            + 'font-family:sans-serif';
          const head = document.createElement('div');
          head.textContent = (car.querySelector('.carousel_heading')?.textContent || 'Carousel')
            + '  —  ' + location.pathname.split('/').slice(1, 3).join(' / ')
            + '  —  ' + new Date().toLocaleString();
          head.style.cssText = 'font:bold 13px sans-serif;margin:0 0 6px';
          sheet.appendChild(head);
          const grid = document.createElement('div');
          grid.style.cssText = 'display:grid;grid-template-columns:repeat(3,1fr);gap:6px';
          [...car.querySelectorAll('a.carousel_view3-parent')].forEach((a, i) => {
            const c = a.cloneNode(true);
            c.removeAttribute('href');
            c.style.cssText = 'position:relative;display:block;width:auto;height:auto;'
              + 'border:1px solid #ddd;border-radius:6px;overflow:hidden;flex:none';
            c.querySelectorAll('img').forEach(im => { im.style.maxHeight = '70px'; im.style.width = 'auto'; });
            const b = document.createElement('div');
            b.textContent = '#' + (i + 1);
            b.style.cssText = 'position:absolute;top:3px;left:3px;z-index:9;background:#d32f2f;'
              + 'color:#fff;font:bold 12px sans-serif;padding:1px 5px;border-radius:4px';
            c.appendChild(b);
            grid.appendChild(c);
          });
          sheet.appendChild(grid);
          document.body.appendChild(sheet);
          return sheet;
        }""")
        el = handle.as_element() if handle else None
        if el is None:
            return ""
        path = os.path.join(settings.LOGS_DIR, f"carousel_{tag}_{_t.strftime('%Y%m%d_%H%M%S')}.png")
        el.screenshot(path=path)
        page.evaluate("() => document.getElementById('__rp_evidence')?.remove()")
        logger.info("📸 Carousel evidence (all cards): %s", path)
        return path
    except Exception as ex:  # noqa: BLE001 — evidence must never mask the real failure
        logger.warning("Could not photograph the carousel: %s", ex)
        return ""


def _rp_cards(page) -> list[dict]:
    """The rendered 'Recommended … For You' cards, in carousel order."""
    cards = _rp_cards_raw(page)
    for c in cards:
        if c.get("ds"):
            continue
        api_ds = None
        if c.get("pid"):
            api_ds = _RP_API_DS.get("pid:" + c["pid"])
        if api_ds is None:
            api_ds = _RP_API_DS.get("nc:" + (c.get("name") or "").strip().lower()
                                    + "|" + (c.get("city") or "").strip().lower())
        if api_ds:
            c["ds"] = True   # AI-video DS: known to the API, no badge on the page
    return cards


def _rp_cards_raw(page) -> list[dict]:
    return page.evaluate("""() => {
      const car = [...document.querySelectorAll('.carousel_parent_short')]
        .find(c => /Recommended (Products|Services) For You/i.test(c.textContent || ''));
      if (!car) return [];
      return [...car.querySelectorAll('a.carousel_view3-parent')].map(a => {
        const m = (a.getAttribute('href') || '').match(/\\/jdmart\\/([^\\/]+)\\//);
        const info = a.querySelector('.carousel_view3-infoDiv');
        const spans = info ? [...info.querySelectorAll('span.carousel_text_wrap')] : [];
        const price = (a.querySelector('.carousel_view3-price') || {}).textContent || '';
        const pm = (a.getAttribute('href') || '').match(/pid-(\d+)/);
        return { name: spans[0] ? spans[0].textContent.trim() : '',
                 company: spans[1] ? spans[1].textContent.trim() : '',
                 pid: pm ? pm[1] : '',
                 city: m ? m[1].replace(/-/g, ' ') : '',
                 ds: !!a.querySelector('.carouselview__img3dicn, .carouselview__img3dwrp'),
                 price: /\\d/.test(price) };
      });
    }""") or []


def store_recommended_position(page, needle: str, variable_name: str) -> None:
    """
    `store position of recommended product "<name or company>" on page as <var>`
    1-based position of the first card whose product name OR company contains
    the text; 0 when it is not in the carousel at all (so a later comparison
    can still run — "not shown" is a legitimate outcome in another city).
    """
    want = resolve_variables(needle).strip().lower()
    cards = _rp_cards(page)
    pos = next((i for i, c in enumerate(cards, 1)
                if want in c["name"].lower() or want in c["company"].lower()), 0)
    RUNTIME_VARIABLES[variable_name] = str(pos)
    logger.info("💾 '%s' is at position %s of %d recommended cards -> ${%s}",
                needle, pos or "none", len(cards), variable_name)


def verify_recommended_prefer_city(page, city: str = "") -> None:
    """
    `verify recommended products prefer search city on page` [`for city "…"`]
    Within each Digital-Showroom group, every card from the search city must
    come before every card from elsewhere. The city is read from the page URL.
    Passes vacuously (with a log line) when no card is from the search city —
    that result simply does not exercise the rule.
    """
    want = _rp_search_city(page, city).lower()
    cards = _rp_cards(page)
    if not cards:
        raise Exception("❌ No recommended products found on the page.")
    in_city = [i for i, c in enumerate(cards, 1) if _rp_same_city(c["city"], want)]
    if not in_city:
        logger.info("ℹ️  No recommended product is from '%s' — city preference not exercised here.", want)
        return
    for ds in (True, False):
        seen_outside = None
        for i, c in enumerate(cards, 1):
            if c["ds"] != ds:
                continue
            if not _rp_same_city(c["city"], want):
                seen_outside = seen_outside or (i, c)
            elif seen_outside:
                shot = _rp_evidence(page, "city")
                raise Exception(
                    (f"All cards as rendered: {shot}\n" if shot else "") +
                    f"❌ City preference broken: #{i} '{c['name'][:45]}' ({c['city']}, the "
                    f"search city) comes after #{seen_outside[0]} "
                    f"'{seen_outside[1]['name'][:45]}' ({seen_outside[1]['city']}) in the "
                    f"same {'DS' if ds else 'non-DS'} group (GJDT-22686).")
    logger.info("✅ City preference holds for '%s': in-city cards at positions %s of %d.",
                want, in_city, len(cards))


def verify_stored_variable_compare(variable_name: str, op: str, other: str) -> None:
    """`verify stored <a> is greater than <b>` / `is less than` / `is at least` / `is at most`."""
    if variable_name not in RUNTIME_VARIABLES:
        raise Exception(f"❌ Variable '{variable_name}' is not stored in memory.")
    try:
        a = float(str(RUNTIME_VARIABLES[variable_name]).strip())
        b = float(str(resolve_variables(other)).strip())
    except ValueError as e:
        raise Exception(f"❌ Both sides must be numbers: ${{{variable_name}}}="
                        f"{RUNTIME_VARIABLES[variable_name]!r}, other={other!r}") from e
    ok = {"greater than": a > b, "less than": a < b, "at least": a >= b, "at most": a <= b}[op]
    if not ok:
        raise Exception(f"❌ Expected ${{{variable_name}}} ({a:g}) to be {op} {b:g}.")
    logger.info("✅ ${%s} (%g) is %s %g.", variable_name, a, op, b)


# ─────────────────────────────────────────────────────────────────────────────
# NETWORK CAPTURE — what the page sent, for click trackers and lead calls
# ─────────────────────────────────────────────────────────────────────────────
#: Requests seen since `start capturing network requests`: (url, method, post body).
_NET_LOG: list[tuple[str, str, str]] = []
_NET_PAGES: set = set()
_NET_TARGETS: list = []


def _net_record(req) -> None:
    try:
        body = req.post_data or ""
    except Exception:  # noqa: BLE001
        body = ""
    _NET_LOG.append((req.url, req.method, body[:4000]))


def start_network_capture(page) -> None:
    """`start capturing network requests` — from here on, every request the
    ACTIVE page makes is remembered (URL, method, body). A tracker such as
    `li=gbp_afp_b2b_plisting_carousel` is only ever visible here."""
    _NET_LOG.clear()
    target = _TEST_SESSION.active_page or page
    if id(target) not in _NET_PAGES:
        target.on("request", _net_record)
        _NET_PAGES.add(id(target))
    _NET_TARGETS.append(target)
    logger.info("🕸️ Network capture started")


def _net_matches(needle: str) -> list[tuple[str, str, str]]:
    want = resolve_variables(needle)
    return [r for r in _NET_LOG if want in r[0] or want in r[2]]


def verify_network_request(needle: str, expected: bool = True, wait_s: float = 10) -> None:
    """`verify network request containing "<text>" was sent` / `was not sent`
    (checks URL and POST body of every captured request; waits up to 10 s for
    an asynchronous tracker to fire before deciding)."""
    def _pause(ms: int) -> None:
        # time.sleep() blocks Playwright's event delivery, so no new requests
        # were recorded while "waiting". Waiting on the page lets them in.
        target = _NET_TARGETS[-1] if _NET_TARGETS else None
        try:
            target.wait_for_timeout(ms) if target is not None else time.sleep(ms / 1000)
        except Exception:  # noqa: BLE001
            time.sleep(ms / 1000)

    if not expected:
        _pause(2000)            # give a late tracker the chance to fire before saying "not sent"
    deadline = time.time() + (wait_s if expected else 0)
    hits = _net_matches(needle)
    while expected and not hits and time.time() < deadline:
        _pause(500)
        hits = _net_matches(needle)
    if expected and not hits:
        sample = "\n".join(f"   {m} {u[:140]}" for u, m, _ in _NET_LOG[-8:]) or "   (none captured)"
        raise Exception(f"❌ No network request containing '{resolve_variables(needle)}' was sent "
                        f"since capture started ({len(_NET_LOG)} captured). Last requests:\n{sample}")
    if not expected and hits:
        raise Exception(f"❌ {len(hits)} network request(s) containing "
                        f"'{resolve_variables(needle)}' were sent, e.g. {hits[0][0][:160]}")
    logger.info("✅ Network request containing '%s': %s (%d match).",
                resolve_variables(needle), "sent" if expected else "not sent", len(hits))


def store_network_request(needle: str, variable_name: str) -> None:
    """`store network request containing "<text>" as <var>` — the full URL of
    the first matching request, so its parameters can be asserted with
    `verify stored <var> contains "…"`."""
    hits = _net_matches(needle)
    if not hits:
        raise Exception(f"❌ No network request containing '{resolve_variables(needle)}' captured.")
    url, method, body = hits[0]
    RUNTIME_VARIABLES[variable_name] = url + (("  BODY:" + body) if body else "")
    logger.info("💾 Network request -> ${%s} = %s %s", variable_name, method, url[:200])


def verify_stored_variable_equals(variable_name, expected_text, ignore_case=False):
    """Assert a stored variable EXACTLY equals the value (spaces at the ends ignored)."""
    if variable_name not in RUNTIME_VARIABLES:
        raise Exception(
            f"❌ Execution Error: Variable '{variable_name}' is not stored in memory. "
            f"Did you run the store step first?")
    stored = str(RUNTIME_VARIABLES[variable_name]).strip()
    expected = str(expected_text).strip()
    ignore_casing = _parse_boolean(ignore_case)
    lhs, rhs = (stored.lower(), expected.lower()) if ignore_casing else (stored, expected)
    logger.info("🔎 Verifying stored '%s' equals '%s'", variable_name, expected)
    if lhs != rhs:
        raise AssertionError(f"❌ Stored '{variable_name}' is '{stored}', expected exactly '{expected}'.")
    logger.info("✅ '%s' equals '%s'", variable_name, expected)


def verify_stored_variable_not_equals(variable_name, unexpected_text, ignore_case=False):
    """Assert a stored variable does NOT exactly equal the given value."""
    if variable_name not in RUNTIME_VARIABLES:
        raise Exception(
            f"❌ Execution Error: Variable '{variable_name}' is not stored in memory. "
            f"Did you run the extraction step first?"
        )
    stored = str(RUNTIME_VARIABLES[variable_name])
    ignore_casing = _parse_boolean(ignore_case)
    lhs = stored.lower() if ignore_casing else stored
    rhs = str(unexpected_text).lower() if ignore_casing else str(unexpected_text)
    logger.info("🔎 Verifying stored '%s' is NOT '%s'", variable_name, unexpected_text)
    if lhs == rhs:
        raise Exception(
            f"❌ Match Failed: stored variable '{variable_name}' still equals "
            f"'{unexpected_text}' (expected it to have changed)."
        )
    logger.info("✅ Stored '%s' differs from '%s' (actual: '%s').",
                variable_name, unexpected_text, stored)


def wait_until_element_text_not(page, locator_name, unexpected_text, timeout_ms: int | None = None):
    """
    Condition-based wait for an element's text to STOP being `unexpected_text`.

    Uses Playwright auto-waiting via expect(...).not_to_have_text with a bounded
    timeout — never a fixed sleep. Needed where an element stays visible while its
    label is still changing, so a visibility wait would return too early.
    """
    selector = _resolve_locator_or_raise(locator_name, page)
    timeout = int(timeout_ms or settings.ACTION_TIMEOUT_MS)
    logger.info("⏳ Waiting up to %dms for '%s' text to differ from '%s'",
                timeout, locator_name, unexpected_text)
    try:
        expect(_get_locator_root(page).locator(selector).first).not_to_have_text(str(unexpected_text), timeout=timeout)
    except AssertionError as e:
        raise Exception(
            f"'{locator_name}' text still equals '{unexpected_text}' after {timeout}ms"
        ) from e
    logger.info("✅ Element '%s' text changed.", locator_name)


# ─────────────────────────────────────────────────────────────────────────────
# CODELESS UI BINDINGS for the visibility / wait / OTP actions
# Same convention as the rest of this file: the core function holds the logic,
# the ui_* wrapper is the registry surface and uses operator-facing parameter
# names so the dashboard can label its inputs.
# ─────────────────────────────────────────────────────────────────────────────

@codeless_snippet("Verify Element Is Visible")
def ui_verify_element_visible(page, locator):
    verify_element_visible(page, locator)


@codeless_snippet("Fetch OTP From Portal")
def ui_fetch_otp_from_portal(page, mobile_number, save_to_variable_name,
                             previous_otp_optional="", timeout_seconds_optional=""):
    fetch_otp_from_portal(
        page, mobile_number, save_to_variable_name,
        after=previous_otp_optional,
        timeout_s=int(timeout_seconds_optional) if timeout_seconds_optional else None,
    )


@codeless_snippet("Verify Element Is Not Present")
def ui_verify_element_not_exists(page, locator, settle_ms_optional=""):
    verify_element_not_exists(page, locator,
                              int(settle_ms_optional) if settle_ms_optional else None)


@codeless_snippet("Verify Element Is Not Visible")
def ui_verify_element_not_visible(page, locator, settle_ms_optional=""):
    verify_element_not_visible(page, locator,
                               int(settle_ms_optional) if settle_ms_optional else None)


@codeless_snippet("Wait Until Element Is Visible")
def ui_wait_until_element_visible(page, locator, timeout_ms_optional=""):
    wait_until_element_visible(page, locator, int(timeout_ms_optional) if timeout_ms_optional else None)


@codeless_snippet("Wait Until Element Text Is Not")
def ui_wait_until_element_text_not(page, locator, unexpected_text, timeout_ms_optional=""):
    wait_until_element_text_not(page, locator, unexpected_text,
                                int(timeout_ms_optional) if timeout_ms_optional else None)


@codeless_snippet("Enter OTP Across Multiple Inputs")
def ui_enter_otp(page, locator, otp_value_or_variable):
    enter_otp(page, otp_value_or_variable, locator)


@codeless_snippet("Verify Stored Variable Is Not")
def ui_verify_stored_var_is_not(page, saved_variable_name, unexpected_text,
                                ignore_case_True_False="False"):
    verify_stored_variable_not_equals(saved_variable_name, unexpected_text,
                                      ignore_case_True_False)



def reset_run_state() -> None:
    """Forget per-run caches so one run can never read another's data."""
    _RP_API_DS.clear()          # carousel DS flags borrowed by the page-order check
    _NET_LOG.clear()            # captured network requests
    _NET_PAGES.clear()          # pages with a request listener attached
    _NET_TARGETS.clear()
