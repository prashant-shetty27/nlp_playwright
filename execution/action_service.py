"""
execution/action_service.py
All Playwright action functions + @codeless_snippet registry bindings.
Extracted from actions.py with updated imports pointing to the new modules.

Global state (RUNTIME_VARIABLES) now lives in nlp.variable_manager.
"""
import re
import logging

from playwright.sync_api import expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from execution.retry import with_retry
from execution.browser_manager import (
    _ensure_dir, _timestamp, get_standard_timeout_ms, get_default_scroll_count,
)
from execution.session import TestSession
from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables
from locators.manager import get_locator_and_dna
from core.healer import ml_heal_element
from core.registry import codeless_snippet
from config.settings import SITES
from config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# TAB / WINDOW & IFRAME STATE (session-scoped)
# ─────────────────────────────────────────────────────────────────────────────
_TEST_SESSION: TestSession = TestSession()


def set_test_session(session: TestSession | None) -> None:
    """Bind action-service state to the active run session."""
    global _TEST_SESSION
    _TEST_SESSION = session or TestSession()


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


def _stabilize_page(page):
    """
    Architectural barrier: waits for SPA/React routing and network stabilization.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
        page.wait_for_timeout(1500)
    except Exception:
        pass


def _get_healed_element_locator(page, locator_name):
    primary_xpath, dna = get_locator_and_dna(locator_name)
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")

    primary_xpath = resolve_variables(primary_xpath)
    root = _get_locator_root(page)   # frame-aware: uses iframe context when active
    loc = root.locator(primary_xpath).first

    if not loc.is_visible(timeout=3000):
        logger.warning("Verification element not immediately visible. Attempting ML heal...")
        if dna:
            try:
                healed_xpath = ml_heal_element(page, dna)  # ML heal always scans the real page DOM
                if healed_xpath:
                    logger.info("Healed verification element successfully!")
                    return root.locator(healed_xpath).first
            except Exception:
                pass
    return loc


# ─────────────────────────────────────────────────────────────────────────────
# OPEN SITE
# ─────────────────────────────────────────────────────────────────────────────
@with_retry(max_attempts=3, delay=2.0)
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
    target_domain = parsed_url.netloc

    if not target_domain:
        raise ValueError(f"Validation Error: '{sanitized_url}' could not be parsed into a valid domain.")

    auth_registry = get_auth_registry()
    if target_domain in auth_registry:
        credentials = auth_registry[target_domain]
        username = credentials.get("username")
        password = credentials.get("password")
        if not username or not password:
            raise ValueError(f"Security Error: Incomplete credentials for domain '{target_domain}'.")
        logger.info(f"🔒 Secure domain '{target_domain}' detected. Injecting HTTP credentials.")
        # Playwright requires credentials embedded in URL for HTTP Basic Auth
        parsed_url = parsed_url._replace(
            netloc=f"{username}:{password}@{target_domain}"
        )
        sanitized_url = parsed_url.geturl()

    logger.info(f"🌐 Navigating to: {sanitized_url}")
    try:
        page.goto(sanitized_url, wait_until="domcontentloaded", timeout=30000)
        # Let dynamic elements finish rendering
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass  # networkidle timeout is non-fatal
    except Exception as e:
        raise RuntimeError(f"Navigation Error: Failed to load '{sanitized_url}'. Details: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CLICK
# ─────────────────────────────────────────────────────────────────────────────
@with_retry(max_attempts=2, delay=1.0)
def click_element(page, locator_name):
    primary_xpath, dna = get_locator_and_dna(locator_name)
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")

    primary_xpath = resolve_variables(primary_xpath)

    try:
        logger.info(f"🖱️ Attempting click on: {locator_name}")
        page.locator(primary_xpath).first.click(timeout=5000)
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
            healed_xpath = ml_heal_element(page, dna)
        except Exception as ml_err:
            raise Exception(f"Self-healing match failed: {ml_err}")

        if healed_xpath:
            page.locator(healed_xpath).first.click(timeout=5000)
            logger.info(f"🏥 Successfully healed and clicked '{locator_name}'!")
            _stabilize_page(page)
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")


# ─────────────────────────────────────────────────────────────────────────────
# FILL
# ─────────────────────────────────────────────────────────────────────────────
@with_retry(max_attempts=2, delay=1.0)
def fill_element(page, text, locator_name):
    primary_xpath, dna = get_locator_and_dna(locator_name)
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")

    primary_xpath = resolve_variables(primary_xpath)

    def execute_robust_fill(xpath):
        loc = page.locator(xpath).first
        try:
            loc.fill(str(text), timeout=3000)
            return True
        except PlaywrightTimeoutError:
            if loc.count() > 0:
                logger.warning("🛡️ Input field blocked. Forcing value via JavaScript...")
                loc.evaluate("(el, v) => { el.value = v; }", text)
                loc.dispatch_event("input")
                return True
            raise

    try:
        logger.info(f"⌨️ Attempting to type '{text}' into: {locator_name}")
        execute_robust_fill(primary_xpath)
        logger.info("✅ Fill successful.")
        _stabilize_page(page)
    except (PlaywrightTimeoutError, PlaywrightError):
        logger.warning("⚠️ Primary input failed. Triggering ML Healer...")
        if not dna:
            raise Exception(f"Element broken and no ML DNA available to heal: {locator_name}")
        try:
            healed_xpath = ml_heal_element(page, dna)
        except Exception as ml_err:
            raise Exception(f"Self-healing match failed: {ml_err}")

        if healed_xpath:
            execute_robust_fill(healed_xpath)
            logger.info(f"Successfully healed and filled '{locator_name}'!")
            _stabilize_page(page)
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────
def extract_element_text(page, locator_name, variable_name):
    primary_xpath, dna = get_locator_and_dna(locator_name)
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")
    primary_xpath = resolve_variables(primary_xpath)

    def execute_extraction(xpath):
        loc = page.locator(xpath).first
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
        healed_xpath = ml_heal_element(page, dna)
        if healed_xpath:
            execute_extraction(healed_xpath)
            logger.info(f"🏥 Successfully healed and extracted text from '{locator_name}'!")
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
    primary_xpath, _ = get_locator_and_dna(locator_name)
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")
    primary_xpath = resolve_variables(primary_xpath)
    count = page.locator(primary_xpath).count()
    RUNTIME_VARIABLES[variable_name] = str(count)
    logger.info(f"💾 EXTRACTED COUNT: {count} elements found -> Stored as '${variable_name}'")


def extract_page_url(page, variable_name):
    url = page.url
    RUNTIME_VARIABLES[variable_name] = str(url)
    logger.info(f"💾 EXTRACTED URL: '{url}' -> Stored as '${variable_name}'")


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
def _dismiss_modal(page_obj, wait_for_popup_ms: int = 6000):
    """
    Waits for a blocking modal (e.g. JustDial login popup) and dismisses it.

    Strategy (in priority order):
      1. Use the stored `maybe_later_link` manual locator (XPath from locators_manual.json)
      2. Try common close-button CSS selectors as a fallback
      3. Press Escape
      4. Force-hide via JavaScript
    """
    # Give the popup time to appear (JustDial fires it ~5 s after page load)
    page_obj.wait_for_timeout(wait_for_popup_ms)

    # ── 1. Try the manual locator ─────────────────────────────────────────────
    MODAL_LOCATOR_NAMES = ["maybe_later_link"]
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
        loc = page.get_by_text(re.compile(pattern, re.IGNORECASE)).first
    else:
        loc = page.get_by_text(str(text), exact=exact_match).first
    expect(loc).to_be_visible(timeout=5000)
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
    if match_text not in src:
        raise Exception(
            f"❌ Match Failed: Could not find '{partial_text}' anywhere inside stored variable "
            f"'{variable_name}' (Current Value: '{stored_text}')"
        )
    logger.info("✅ Stored Variable Partial Match Success.")


# ─────────────────────────────────────────────────────────────────────────────
# WAITS & SCROLLS
# ─────────────────────────────────────────────────────────────────────────────
def wait_for_result_page_load(page):
    try:
        page.wait_for_selector(".result-content-container", timeout=15000)
        logger.info("✅ Result page successfully loaded.")
    except Exception:
        logger.warning("⚠️ Results container not detected.")


def wait_seconds(page, seconds: float):
    page.wait_for_timeout(float(seconds) * 1000)


def refresh_page(page):
    page.reload(wait_until="load")


def scroll_to_element(page, target: str) -> None:
    """Scroll until the named element is visible in the viewport."""
    from locators.manager import get_locator_and_dna
    xpath, _ = get_locator_and_dna(target)
    if not xpath:
        raise Exception(f"Locator '{target}' not found for scroll_to")
    page.locator(xpath).first.scroll_into_view_if_needed(timeout=8000)
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
            page.wait_for_timeout(float(scroll_wait) * 1500)
    return False


def take_screenshot(page_obj, label="capture"):
    import os
    if not settings.ENABLE_SCREENSHOTS:
        logger.info("📵 Screenshots are disabled (ENABLE_SCREENSHOTS=false). Skipping capture '%s'.", label)
        return
    _ensure_dir(settings.SCREENSHOTS_DIR)
    filename = os.path.join(settings.SCREENSHOTS_DIR, f"{label}_{_timestamp()}.png")

    # A full-page capture has to stitch the whole scroll height, which on tall
    # lazy-loading pages can outlast the action timeout. Screenshots are diagnostic
    # output, so fall back to the viewport rather than failing the step outright.
    try:
        page_obj.screenshot(path=filename, full_page=True,
                            timeout=settings.SCREENSHOT_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.warning(
            "⏱️  Full-page screenshot '%s' timed out after %dms — capturing viewport instead.",
            label, settings.SCREENSHOT_TIMEOUT_MS,
        )
        page_obj.screenshot(path=filename, full_page=False,
                            timeout=settings.SCREENSHOT_TIMEOUT_MS)

    logger.info("📸 Screenshot Saved: %s", filename)


# ─────────────────────────────────────────────────────────────────────────────
# TAB / WINDOW MANAGEMENT (Playwright BrowserContext)
# ─────────────────────────────────────────────────────────────────────────────

def switch_tab(page, index: int):
    """Focus a browser tab by 0-based index."""
    pages = page.context.pages
    if index < 0 or index >= len(pages):
        raise AssertionError(
            f"❌ Tab index {index} out of range. "
            f"Open tabs: {len(pages)}  (valid: 0–{len(pages) - 1})"
        )
    _TEST_SESSION.active_page = pages[index]
    _TEST_SESSION.active_frame = None
    _TEST_SESSION.active_page.bring_to_front()
    logger.info("🪟 Switched to tab %d — %s", index, _TEST_SESSION.active_page.url)


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
        _TEST_SESSION.active_page.url if _TEST_SESSION.active_page else "—",
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
        _TEST_SESSION.active_page.url if _TEST_SESSION.active_page else "—",
    )


def open_new_tab(page):
    """Open a blank new tab and switch focus to it."""
    _TEST_SESSION.active_page = page.context.new_page()
    _TEST_SESSION.active_frame = None
    logger.info("🪟 Opened new tab (now active).")


def list_tabs(page):
    """Log every open tab with its index, title, and URL."""
    pages = page.context.pages
    logger.info("📋 Open tabs (%d):", len(pages))
    for i, p in enumerate(pages):
        try:
            title = p.title()
        except Exception:
            title = "—"
        logger.info("  [%d] %s  —  %s", i, title, p.url)


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
        from locators.manager import get_locator_and_dna
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
