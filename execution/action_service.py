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
from nlp.variable_manager import RUNTIME_VARIABLES, resolve_variables
from locators.manager import get_locator_and_dna
from core.healer import ml_heal_element
from core.registry import codeless_snippet
from config.settings import SITES
from config import settings

logger = logging.getLogger(__name__)


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
    loc = page.locator(primary_xpath).first

    if not loc.is_visible(timeout=3000):
        logger.warning("Verification element not immediately visible. Attempting ML heal...")
        if dna:
            try:
                healed_xpath = ml_heal_element(page, dna)
                if healed_xpath:
                    logger.info("Healed verification element successfully!")
                    return page.locator(healed_xpath).first
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
        logger.warning("⚠️ Primary locator failed. Triggering ML Healer...")
        if not dna:
            raise Exception(f"Element broken and no ML DNA available: {locator_name}")
        try:
            healed_xpath = ml_heal_element(page, dna)
        except Exception as ml_err:
            raise Exception(f"Self-healing math failed: {ml_err}")

        if healed_xpath:
            page.locator(healed_xpath).first.click(timeout=5000)
            logger.info(f"🏥 Successfully healed and clicked '{locator_name}'!")
            _stabilize_page(page)
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")


# ─────────────────────────────────────────────────────────────────────────────
# FILL
# ─────────────────────────────────────────────────────────────────────────────
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
                loc.evaluate(f"el => el.value = '{text}'")
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
            raise Exception(f"Self-healing math failed: {ml_err}")

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
    page_obj.screenshot(path=filename, full_page=True)
    logger.info("📸 Screenshot Saved: %s", filename)


# ─────────────────────────────────────────────────────────────────────────────
# CODELESS UI API (REGISTRY BINDINGS)
# ─────────────────────────────────────────────────────────────────────────────

@codeless_snippet("Open Site")
def ui_open_site(page, target_url_or_key: str):
    target = target_url_or_key.lower().strip()
    if target.startswith("http://") or target.startswith("https://"):
        url_to_open = target
    elif target in SITES:
        url_to_open = SITES[target]
    else:
        raise ValueError(f"❌ Unknown site or invalid URL: {target}")
    page.goto(url_to_open, wait_until="load")
    logger.info(f"🌐 Page Loaded: {url_to_open}")
    _stabilize_page(page)


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
