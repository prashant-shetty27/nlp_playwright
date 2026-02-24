from utils import with_retry
import os
import re
import json
import logging
import tempfile

from datetime import datetime
from urllib.parse import urlparse

# Third-party Image & ML Libraries
import cv2
import numpy as np
import imagehash
from PIL import Image
from skimage.metrics import structural_similarity as ssim

# Playwright
from playwright.sync_api import sync_playwright, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

# Local Framework Modules
from config_properties.site_registry import SITES
from config_properties.constants import default_scroll_limit, wait_timeout_ms
from locator_manager import get_locator_and_dna, get_locator_path
from healer import ml_heal_element
from registry import codeless_snippet

logger = logging.getLogger(__name__)

# --- GLOBAL BROWSER & RUNTIME STATE ---
_playwright_instance = None
_browser = None
_context = None
RUNTIME_VARIABLES = {} 

# ==========================================
# CORE CONFIGURATION MANAGER
# ==========================================
def load_auth_registry(config_path: str = "env_config.json") -> dict:
    if not os.path.exists(config_path):
        logging.warning(f"⚠️ Auth registry '{config_path}' missing. Proceeding without environment credentials.")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        # Crash early if the configuration file is mangled.
        raise ValueError(f"Architecture Error: '{config_path}' is corrupted or contains invalid JSON. {e}")

# Load into memory once during module initialization to prevent repetitive disk I/O
AUTH_REGISTRY = load_auth_registry()

# Apply the decorator to automatically retry this specific function up to 3 times
@with_retry(max_attempts=3, delay=2.0)
def open_site(page, url: str):
    # --- PHASE 1: Sanitization & Structural Validation ---
    if not url or not isinstance(url, str):
        raise ValueError("Validation Error: 'url' parameter must be a non-empty string.")
    
    # ... (The rest of your exact open_site logic remains untouched here) ...
# ==========================================
# ACTION: OPEN SITE
# ==========================================
def open_site(page, url: str):
    # --- PHASE 1: Sanitization & Structural Validation ---
    if not url or not isinstance(url, str):
        raise ValueError("Validation Error: 'url' parameter must be a non-empty string.")

    raw_url = url.strip()

    if " " in raw_url:
        raise ValueError(f"Validation Error: URL cannot contain spaces. Received: '{raw_url}'")
        
    if "." not in raw_url:
        raise ValueError(f"Routing Error: '{raw_url}' lacks a domain structure (e.g., '.com'). Single words are rejected.")

    # Enforce secure protocol by default
    if not raw_url.startswith(("http://", "https://")):
        sanitized_url = f"https://{raw_url}"
    else:
        sanitized_url = raw_url

    parsed_url = urlparse(sanitized_url)
    target_domain = parsed_url.netloc

    if not target_domain:
        raise ValueError(f"Validation Error: '{sanitized_url}' could not be parsed into a valid domain.")

    # --- PHASE 2: Contextual Authentication ---
    if target_domain in AUTH_REGISTRY:
        credentials = AUTH_REGISTRY[target_domain]
        username = credentials.get("username")
        password = credentials.get("password")

        if not username or not password:
            raise ValueError(f"Security Error: Incomplete credentials found for domain '{target_domain}' in registry.")

        logging.info(f"🔒 Secure domain '{target_domain}' detected. Injecting Playwright HTTP credentials.")
        
        # Playwright natively intercepts the 401 challenge and answers with these credentials
        page.context.set_http_credentials({
            "username": username,
            "password": password
        })
    else:
        # Purge credentials if navigating to an unsecured domain to prevent credential leakage
        page.context.set_http_credentials(None)

    # --- PHASE 3: Navigation Execution ---
    logging.info(f"🌐 Navigating to: {sanitized_url}")
    
    try:
        # Enforce a strict timeout (30 seconds) to prevent hanging the runner
        page.goto(sanitized_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        raise RuntimeError(f"Navigation Error: Failed to load '{sanitized_url}'. Details: {e}")

def resolve_variables(text):
    """
    THE INTERPOLATION ENGINE
    1. Checks if the raw text perfectly matches a stored variable name.
    2. Scans for ${var_name} syntax for inline string replacement.
    """
    if not isinstance(text, str):
        return text
    
    clean_text = text.strip()
    
    # --- UPGRADE: Exact match fallback ---
    # If the user typed 'search_text' without the ${}, catch it automatically
    if clean_text in RUNTIME_VARIABLES:
        return str(RUNTIME_VARIABLES[clean_text])
        
    # --- Standard Interpolation ---
    matches = re.findall(r'\$\{([^}]+)\}', text)
    result = text
    for var_name in matches:
        if var_name in RUNTIME_VARIABLES:
            result = result.replace(f"${{{var_name}}}", str(RUNTIME_VARIABLES[var_name]))
        else:
            raise ValueError(f"❌ Execution Error: Variable '${{{var_name}}}' is not stored in memory!")
    return result
def _parse_boolean(val):
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ['true', 'yes', '1', 'y']

def _stabilize_page(page):
    """
    ARCHITECTURAL BARRIER: 
    Forces the automation engine to wait for SPA/React routing and network stabilization
    before executing the next step. Prevents false-positive race conditions.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
        # 1.5 second physical buffer to allow heavy JS event loops to finish painting the UI
        page.wait_for_timeout(1500) 
    except Exception:
        pass # Safely ignore if the state is already idle

# ==========================================
# 1. CONFIGURATION & UTILITIES
# ==========================================

def load_playwright_config():
    path = "playwright.config.json"
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error("❌ Failed to load playwright.config.json: %s", e)

    return {
        "use": {
            "headless": False,
            "actionTimeout": 15000,
            "navigationTimeout": 30000,
            "resolution_mode": "auto",
        }
    }

def _ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def _timestamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def get_system_resolution():
    return 1920, 1080

def _get_standard_timeout_ms():
    cfg = load_playwright_config()
    return int(cfg.get("use", {}).get("actionTimeout", 15000))

def _get_default_scroll_count():
    cfg = load_playwright_config()
    try:
        return int(cfg.get("run", {}).get("default_scroll_count", default_scroll_limit))
    except Exception:
        return default_scroll_limit

# ==========================================
# 2. BROWSER LIFECYCLE
# ==========================================

def open_browser():
    global _browser, _context, _playwright_instance
    full_config = load_playwright_config()
    use = full_config.get("use", {})

    w, h = get_system_resolution()
    logger.info("🖥️ Desktop Resolution: %sx%s.", w, h)

    _playwright_instance = sync_playwright().start()
    _browser = _playwright_instance.chromium.launch(
        headless=use.get("headless", False),
        args=["--start-maximized", "--disable-infobars"],
    )
    _context = _browser.new_context(
        no_viewport=True,
        permissions=use.get("permissions", []),
    )
    _context.set_default_timeout(use.get("actionTimeout", 15000))
    page = _context.new_page()

    logger.info("🚀 Session Started | Browser Ready")
    return page

def close_browser(page, test_name="test_run"):
    global _browser, _context, _playwright_instance
    video_path = None
    try:
        if page and page.video:
            video_path = page.video.path()
    except Exception as e:
        logger.debug("No video found or error accessing video path: %s", e)

    try:
        if _context: _context.close()
        if _browser: _browser.close()
        if _playwright_instance: _playwright_instance.stop()
    except Exception as e:
        logger.warning("Browser close issue: %s", e)

    if video_path and os.path.exists(video_path):
        _ensure_dir("videos/completed")
        new_path = os.path.join("videos/completed", f"run_{test_name}_{_timestamp()}.webm")
        os.rename(video_path, new_path)
        logger.info("🎥 Final Video: %s", new_path)

# ==========================================
# 3. CORE LOGIC (THE MUSCLE)
# ==========================================

def click_element(page, locator_name):
    primary_xpath, dna = get_locator_and_dna(locator_name)
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")
        
    primary_xpath = resolve_variables(primary_xpath)

    try:
        logger.info(f"🖱️ Attempting click on: {locator_name}")
        page.locator(primary_xpath).first.click(timeout=5000)
        logger.info(f"✅ Click successful.")
        _stabilize_page(page) # Synchronize state
    except (PlaywrightTimeoutError, PlaywrightError) as play_err:
        logger.warning(f"⚠️ Primary locator failed. Triggering ML Healer...")
        if not dna:
            raise Exception(f"Element broken and no ML DNA available: {locator_name}")
        try:
            healed_xpath = ml_heal_element(page, dna)
        except Exception as ml_err:
            raise Exception(f"Self-healing math failed: {ml_err}")
        
        if healed_xpath:
            try:
                page.locator(healed_xpath).first.click(timeout=5000)
                logger.info(f"🏥 Successfully healed and clicked '{locator_name}'!")
                _stabilize_page(page) # Synchronize state
            except Exception as retry_err:
                raise Exception(f"Both primary and ML-healed clicks failed for: {locator_name}")
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")

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
                logger.warning(f"🛡️ Input field blocked. Forcing value via JavaScript...")
                loc.evaluate(f"el => el.value = '{text}'")
                loc.dispatch_event("input") 
                return True
            raise 

    try:
        logger.info(f"⌨️ Attempting to type '{text}' into: {locator_name}")
        execute_robust_fill(primary_xpath)
        logger.info(f"✅ Fill successful.")
        _stabilize_page(page) # Synchronize state
    except (PlaywrightTimeoutError, PlaywrightError) as play_err:
        logger.warning(f"⚠️ Primary input failed. Triggering ML Healer...")
        if not dna:
            raise Exception(f"Element broken and no ML DNA available to heal: {locator_name}")
        try:
            healed_xpath = ml_heal_element(page, dna)
        except Exception as ml_err:
            raise Exception(f"Self-healing math failed: {ml_err}")
        
        if healed_xpath:
            try:
                execute_robust_fill(healed_xpath)
                logger.info(f"Successfully healed and filled '{locator_name}'!")
                _stabilize_page(page) # Synchronize state
            except Exception as retry_err:
                raise Exception(f"Both primary and ML-healed fills failed for: {locator_name}")
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")    

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
    except (PlaywrightTimeoutError, PlaywrightError) as play_err:
        logger.warning(f"⚠️ Primary read failed. Triggering ML Healer...")
        if not dna:
            raise Exception(f"Element broken and no ML DNA available: {locator_name}")
        try:
            healed_xpath = ml_heal_element(page, dna)
        except Exception as ml_err:
            raise Exception(f"Self-healing math failed: {ml_err}")
        if healed_xpath:
            try:
                execute_extraction(healed_xpath)
                logger.info(f"🏥 Successfully healed and extracted text from '{locator_name}'!")
            except Exception as retry_err:
                raise Exception(f"Both primary and ML-healed reads failed for: {locator_name}")
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")

def extract_element_attribute(page, locator_name, attribute_name, variable_name):
    """Extracts HTML attributes like 'href', 'src', or 'class'."""
    loc = _get_healed_element_locator(page, locator_name)
    val = loc.get_attribute(attribute_name)
    if val is None:
        logger.warning(f"⚠️ Attribute '{attribute_name}' not found on '{locator_name}'. Storing empty string.")
        val = ""
    RUNTIME_VARIABLES[variable_name] = str(val).strip()
    logger.info(f"💾 EXTRACTED ATTRIBUTE: '{val}' -> Stored as '${variable_name}'")

def extract_input_value(page, locator_name, variable_name):
    """Extracts text typed into an <input> or <textarea> (inner_text does not work on these)."""
    loc = _get_healed_element_locator(page, locator_name)
    val = loc.input_value(timeout=5000)
    RUNTIME_VARIABLES[variable_name] = str(val).strip()
    logger.info(f"💾 EXTRACTED INPUT: '{val}' -> Stored as '${variable_name}'")

def extract_element_count(page, locator_name, variable_name):
    """Counts how many elements match the locator on the page."""
    primary_xpath, _ = get_locator_and_dna(locator_name)
    primary_xpath = resolve_variables(primary_xpath)
    
    # We don't use the healer here because we specifically want to count the raw matches
    count = page.locator(primary_xpath).count()
    RUNTIME_VARIABLES[variable_name] = str(count)
    logger.info(f"💾 EXTRACTED COUNT: {count} elements found -> Stored as '${variable_name}'")

def extract_page_url(page, variable_name):
    """Stores the current browser URL."""
    url = page.url
    RUNTIME_VARIABLES[variable_name] = str(url)
    logger.info(f"💾 EXTRACTED URL: '{url}' -> Stored as '${variable_name}'")

def extract_page_title(page, variable_name):
    """Stores the current browser Tab Title."""
    title = page.title()
    RUNTIME_VARIABLES[variable_name] = str(title)
    logger.info(f"💾 EXTRACTED TITLE: '{title}' -> Stored as '${variable_name}'")

def create_custom_variable(value, variable_name):
    """Allows the user to store a hardcoded string or number directly into memory."""
    val = resolve_variables(str(value)) # Resolves in case they combine variables
    RUNTIME_VARIABLES[variable_name] = val
    logger.info(f"💾 CREATED VARIABLE: '{val}' -> Stored as '${variable_name}'")

    
def search(page_obj, text: str):
    term = str(text).strip()
    selectors = ["#srchbx", "#main_search", "input[name='search']", "input.search-input", "input[placeholder*='Search']"]
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

    search_box.click()
    search_box.press("Control+A")
    search_box.press("Delete")
    search_box.type(term, delay=80)

    try:
        suggestion = page_obj.locator("li").filter(has_text=re.compile(term, re.IGNORECASE)).first
        if suggestion.is_visible(timeout=1500):
            suggestion.click()
            logger.info("Selected suggestion")
        else:
            search_box.press("Enter")
    except Exception:
        search_box.press("Enter")
        
    logger.info("⏳ Waiting for UI state transition...")
    _stabilize_page(page_obj) # MANDATORY BARRIER: Wait for the new page to actually load
    
    try:
        page_obj.wait_for_selector("div.resultbox_info, .resultbox, .jd_search_result", timeout=5000)
        logger.info("Business listings detected")
    except PlaywrightTimeoutError:
        pass # Let the verification steps handle missing data
        
    return True

def store_specific_data_type(raw_value, data_type, variable_name):
    """
    Intelligently cleans and casts raw strings into strict Python data types.
    """
    # 1. If the user passes a stored variable (e.g., ${price}), resolve it first
    val = resolve_variables(str(raw_value))
    
    try:
        if data_type == "integer" or data_type == "decimal":
            # Smart Numeric Extraction: Strip commas and find the first hidden number
            clean_text = val.replace(',', '')
            matches = re.findall(r'-?\d+\.?\d*', clean_text)
            if not matches:
                raise ValueError(f"No numeric values found in '{val}'")
                
            extracted_num = float(matches[0])
            
            if data_type == "integer":
                RUNTIME_VARIABLES[variable_name] = int(extracted_num)
            else:
                RUNTIME_VARIABLES[variable_name] = extracted_num
                
        elif data_type == "alphanumeric":
            # Strips all spaces, symbols, and punctuation
            clean_val = re.sub(r'[^a-zA-Z0-9]', '', val)
            RUNTIME_VARIABLES[variable_name] = clean_val
            
        elif data_type == "boolean":
            # Safely catches truthy strings
            RUNTIME_VARIABLES[variable_name] = val.strip().lower() in ['true', 'yes', '1', 'y', 't']
            
        elif data_type == "list":
            # Converts "apple, banana, pear" into a strict Python List object
            RUNTIME_VARIABLES[variable_name] = [x.strip() for x in val.split(',') if x.strip()]
            
        else: # Default String
            RUNTIME_VARIABLES[variable_name] = str(val)
            
        logger.info(f"💾 DATA CASTING: Saved {data_type.upper()} -> '{RUNTIME_VARIABLES[variable_name]}' (Stored as '${variable_name}')")
        
    except ValueError as e:
        raise Exception(f"❌ Type Casting Error: Could not convert '{val}' into {data_type}. {e}")

# --- STRING MANIPULATION & MATH ---

def replace_special_chars(source_text, chars_to_remove, target_variable):
    # 1. Extract the specific characters
    pattern = f"[{re.escape(chars_to_remove)}]"
    cleaned_text = re.sub(pattern, "", str(source_text))
    
    # 2. Architectural Whitespace Normalization
    normalized_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    # 3. Store the finalized state
    RUNTIME_VARIABLES[target_variable] = normalized_text
    logger.info(f"💾 Cleaned Text: '{normalized_text}' -> Stored as '${target_variable}'")

def split_and_store_text(source_text, delimiter, index, target_variable):
    parts = source_text.split(delimiter)
    try:
        extracted_part = parts[int(index)].strip()
        RUNTIME_VARIABLES[target_variable] = extracted_part
        logger.info(f"💾 Split Text: '{extracted_part}' -> Stored as '${target_variable}'")
    except IndexError:
        raise Exception(f"Split Error: Cannot grab position {index}. String only split into {len(parts)} parts.")
    except ValueError:
        raise Exception(f"Split Error: Index '{index}' must be a valid number (e.g., 0, 1, 2).")

def concatenate_text(text1, text2, target_variable):
    combined = f"{text1}{text2}"
    RUNTIME_VARIABLES[target_variable] = combined
    logger.info(f"💾 Concatenated: '{combined}' -> Stored as '${target_variable}'")

def _get_numeric_value(input_val):
    """
    Helper function: Intelligently converts user input into a mathematical number.
    Handles raw variable names, commas, and literal strings.
    """
    input_str = str(input_val).strip()
    
    # 1. Check if the user typed a raw variable name (e.g., 'my_price' instead of '${my_price}')
    if input_str in RUNTIME_VARIABLES:
        input_str = str(RUNTIME_VARIABLES[input_str])
        
    # 2. Sanitize the string (remove commas like in "1,500" or currency symbols if they sneak in)
    input_str = input_str.replace(',', '').replace('$', '').replace('₹', '').strip()
    
    try:
        return float(input_str)
    except ValueError:
        raise Exception(f"Math Parse Error: Cannot convert '{input_val}' (resolved to '{input_str}') into a valid number.")

def execute_math(num1, operator, num2, target_variable):
    """Executes math using the Smart Numeric Parser."""
    try:
        # Pass both inputs through the smart parser
        n1 = _get_numeric_value(num1)
        n2 = _get_numeric_value(num2)
        
        if operator == '+': res = n1 + n2
        elif operator == '-': res = n1 - n2
        elif operator == '*': res = n1 * n2
        elif operator == '/': 
            if n2 == 0: raise Exception("Cannot divide by zero.")
            res = n1 / n2
        else: 
            raise Exception("Invalid operator. Use +, -, *, or /")
        
        # Keep the output clean: if it's 4.0, make it 4
        if res.is_integer():
            res = int(res)
            
        RUNTIME_VARIABLES[target_variable] = str(res)
        logger.info(f"💾 Math Result: {n1} {operator} {n2} = '{res}' -> Stored as '${target_variable}'")
    except Exception as e:
        raise Exception(f"Math Operation Failed: {e}")

# ==========================================
# 4. VERIFICATION ENGINES (THE ASSERTIONS)
# ==========================================

def _get_healed_element_locator(page, locator_name):
    primary_xpath, dna = get_locator_and_dna(locator_name)
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")
        
    primary_xpath = resolve_variables(primary_xpath)
    loc = page.locator(primary_xpath).first
    
    if not loc.is_visible(timeout=3000):
        logger.warning(f"Verification element not immediately visible. Attempting ML heal...")
        if dna:
            try:
                healed_xpath = ml_heal_element(page, dna)
                if healed_xpath:
                    logger.info(f"Healed verification element successfully!")
                    return page.locator(healed_xpath).first
            except Exception:
                pass
    return loc

def verify_global_exact_text(page, text: str, ignore_case=False):
    ignore_casing = _parse_boolean(ignore_case)
    logger.info(f"🔎 Verifying EXACT text exists globally: '{text}' (Ignore Case: {ignore_casing})")
    
    if ignore_casing:
        loc = page.get_by_text(re.compile(f"^{re.escape(str(text))}$", re.IGNORECASE)).first
    else:
        loc = page.get_by_text(str(text), exact=True).first
        
    expect(loc).to_be_visible(timeout=5000)
    logger.info("Global exact match confirmed.")

def verify_element_exact_text(page, locator_name, expected_text, ignore_case=False):
    ignore_casing = _parse_boolean(ignore_case)
    logger.info(f"🔎 Verifying EXACT text '{expected_text}' inside '{locator_name}' (Ignore Case: {ignore_casing})")
    loc = _get_healed_element_locator(page, locator_name)
    expect(loc).to_have_text(str(expected_text), ignore_case=ignore_casing, timeout=5000)
    logger.info("Element exact match confirmed.")

def verify_element_contains_text(page, locator_name, partial_text, ignore_case=False):
    ignore_casing = _parse_boolean(ignore_case)
    logger.info(f"🔎 Verifying partial text '{partial_text}' inside '{locator_name}' (Ignore Case: {ignore_casing})")
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
                loc = page.get_by_text(re.compile(f"^{re.escape(text)}$", re.IGNORECASE)).first
            else:
                loc = page.get_by_text(text, exact=True).first
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
    logger.info(f"✅ Variable Text Match Success.")

def verify_stored_variable_contains(variable_name, partial_text, ignore_case=False):
    """
    Looks up a variable directly from memory and checks if it contains a partial substring anywhere inside it.
    """
    if variable_name not in RUNTIME_VARIABLES:
        raise Exception(f"❌ Execution Error: Variable '{variable_name}' is not stored in memory. Did you run the extraction step first?")
        
    stored_text = str(RUNTIME_VARIABLES[variable_name])
    ignore_casing = _parse_boolean(ignore_case)
    
    logger.info(f"🔎 Verifying stored variable '{variable_name}' contains: '{partial_text}' (Ignore Case: {ignore_casing})")
    
    src = stored_text.lower() if ignore_casing else stored_text
    match = str(partial_text).lower() if ignore_casing else str(partial_text)
    
    if match not in src:
        raise Exception(f"❌ Match Failed: Could not find '{partial_text}' anywhere inside stored variable '{variable_name}' (Current Value: '{stored_text}')")
        
    logger.info(f"✅ Stored Variable Partial Match Success.")
# --- WAITS & SCROLLS ---
def wait_for_result_page_load(page):
    try:
        page.wait_for_selector(".result-content-container", timeout=15000)
        logger.info("✅ Result page successfully loaded.")
    except Exception:
        logger.warning("⚠️ Results container not detected.")

def vertical_scroll(page_obj, amount=500):
    page_obj.mouse.wheel(0, int(amount))
    logger.info("📜 Scrolled down by %s pixels", amount)

def scroll_until_text_visible(page, text, max_scrolls=None, scroll_wait=2):
    if max_scrolls is None: max_scrolls = _get_default_scroll_count()
    scrolls = 0
    target_text = str(text).strip('"').strip("'")
    
    while scrolls < int(max_scrolls):
        locator = page.get_by_text(target_text, exact=True)
        if locator.count() > 0 and locator.first.is_visible(timeout=500):
            return True
        page.mouse.wheel(0, 500)
        scrolls += 1
        if scroll_wait: page.wait_for_timeout(float(scroll_wait) * 1500)
    return False

def take_screenshot(page_obj, label="capture"):
    _ensure_dir("screenshots")
    filename = f"screenshots/{label}_{_timestamp()}.png"
    page_obj.screenshot(path=filename, full_page=True)
    logger.info("📸 Screenshot Saved: %s", filename)


# ==========================================
# 5. CODELESS UI API (THE REGISTRY BINDINGS)
# ==========================================

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
    page.wait_for_selector(locator, state=state, timeout=_get_standard_timeout_ms())

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
def ui_math(page, first_number, operator_symbol, second_number, save_to_variable_name):
    execute_math(first_number, operator_symbol, second_number, save_to_variable_name)
    
@codeless_snippet("Math Operation")
def ui_math(page, first_number_or_variable_name, operator_symbol, second_number_or_variable_name, save_to_variable_name):
    execute_math(first_number_or_variable_name, operator_symbol, second_number_or_variable_name, save_to_variable_name)    
    
# --- NEW EXTRACTION API ---

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