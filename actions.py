import os
import json
import logging
import tempfile
import tkinter as tk
from datetime import datetime

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

# --- GLOBAL BROWSER STATE ---
_playwright_instance = None
_browser = None
_context = None


# --- CONFIGURATION & UTILITIES ---

def load_playwright_config():
    """Reads the standardized playwright.config.json with JS/TS style structure."""
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
    """Creates directory if it doesn't exist."""
    if not os.path.exists(path):
        os.makedirs(path)

def _timestamp():
    """Returns current timestamp for file naming."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def get_system_resolution():
    """Detects the actual monitor resolution dynamically."""
    try:
        root = tk.Tk()
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
        root.destroy()
        return w, h
    except Exception:
        return 1920, 1080


# --- BROWSER LIFECYCLE ---

def open_browser():
    """Initializes browser and returns a Playwright page."""
    global _browser, _context, _playwright_instance
    
    full_config = load_playwright_config()
    use = full_config.get("use", {})

    if use.get("resolution_mode") == "auto":
        w, h = get_system_resolution()
        logger.info("🖥️ Auto-Resolution: %sx%s detected.", w, h)
    else:
        viewport = use.get("viewport", {"width": 1280, "height": 720})
        w, h = viewport["width"], viewport["height"]

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
    """Safely tears down the Playwright session and saves videos."""
    global _browser, _context, _playwright_instance

    video_path = None
    try:
        if page and page.video:
            video_path = page.video.path()
    except Exception as e:
        logger.debug("No video found or error accessing video path: %s", e)

    try:
        if _context:
            _context.close()
        if _browser:
            _browser.close()
        if _playwright_instance:
            _playwright_instance.stop()
    except Exception as e:
        logger.warning("Browser close issue: %s", e)

    if video_path and os.path.exists(video_path):
        _ensure_dir("videos/completed")
        new_path = os.path.join(
            "videos/completed",
            f"run_{test_name}_{_timestamp()}.webm"
        )
        os.rename(video_path, new_path)
        logger.info("🎥 Final Video: %s", new_path)


# --- CORE ACTIONS ---

def open_site(page, target: str):
    """DSL command: open <site>"""
    target = target.lower().strip()
    if target not in SITES:
        raise ValueError(f"❌ Unknown site: {target}")
    page.goto(SITES[target], wait_until="load")
    logger.info(f"🌐 Page Loaded: {target.capitalize()}")
    wait_seconds(page, 2)

def refresh_page(page):
    """
    DSL command: refresh page
    Reloads the current page and waits for the DOM to fully load.
    """
    logger.info("🔄 Refreshing the page...")
    page.reload(wait_until="load")
    logger.info("✅ Page refreshed successfully.")
        
def search(page_obj, text: str):
    term = str(text).strip()
    selectors = [
        "#srchbx",
        "#main_search",
        "input[name='search']",
        "input.search-input",
        "input[placeholder*='Search']",
    ]

    search_box = None

    # Locate search input safely
    for selector in selectors:
        try:
            el = page_obj.locator(selector).first
            if el.is_visible(timeout=3000):
                search_box = el
                logger.info("🎯 Search input found: %s", selector)
                break
        except Exception:
            continue # Silently move to the next selector

    if not search_box:
        raise Exception("❌ Search input not found")

    # Perform search
    search_box.click()
    search_box.press("Control+A")
    search_box.press("Delete")
    search_box.type(term, delay=80)

    # Handle suggestion dropdown safely
    try:
        suggestion = page_obj.locator("li").filter(has_text=term).first
        if suggestion.is_visible(timeout=1500):
            suggestion.click()
            logger.info("✅ Selected suggestion")
        else:
            search_box.press("Enter")
    except Exception:
        search_box.press("Enter")
        
    page_obj.wait_for_load_state("networkidle", timeout=5000)
    
    # -------- Result Detection --------
    try:
        page_obj.wait_for_selector("div.resultbox_info, .resultbox", timeout=5000)
        logger.info("📊 Business listings detected")
    except PlaywrightTimeoutError:
        logger.warning("Result container not detected. Checking DOM snapshot...")
        if page_obj.locator("a.resultbox_title_anchor").count() == 0:
            raise Exception("❌ No results detected after search.")

    logger.info("🔍 Search completed successfully")
    return True

def click_element(page, locator_name):
    """Executes a click. If it fails, attempts self-healing using ML DNA."""
    primary_xpath, dna = get_locator_and_dna(locator_name)
    
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")

    try:
        logger.info(f"🖱️ Attempting strict click on: {locator_name}")
        page.locator(primary_xpath).first.click(timeout=5000)
        logger.info(f"✅ Click successful.")

    except (PlaywrightTimeoutError, PlaywrightError) as play_err:
        logger.warning(f"⚠️ Primary locator failed ({type(play_err).__name__}). Triggering ML Healer...")
        
        if not dna:
            logger.error(f"❌ Cannot heal '{locator_name}'. This is a manual locator with no ML DNA.")
            raise Exception(f"Element broken and no ML DNA available to heal: {locator_name}")

        try:
            healed_xpath = ml_heal_element(page, dna)
        except Exception as ml_err:
            logger.error(f"🧨 ML Engine crashed during calculation: {ml_err}")
            raise Exception(f"Self-healing math failed: {ml_err}")
        
        if healed_xpath:
            logger.info(f"✨ ML generated fallback XPath: {healed_xpath}")
            try:
                page.locator(healed_xpath).first.click(timeout=5000)
                logger.info(f"🏥 Successfully healed and clicked '{locator_name}'!")
            except Exception as retry_err:
                logger.error(f"❌ ML fallback also failed: {retry_err}")
                raise Exception(f"Both primary and ML-healed clicks failed for: {locator_name}")
        else:
            logger.error(f"❌ ML Engine could not find a confident match for '{locator_name}'.")
            raise Exception(f"Self-healing failed for: {locator_name}")


# --- WAITS & SCROLLING ---

def wait_seconds(page, seconds: float):
    """DSL command: wait X seconds."""
    page.wait_for_timeout(float(seconds) * 1000)
    logger.info("⏳ Waited for %s seconds", seconds)

def wait_for_result_page_load(page):
    """Waits for results container."""
    try:
        page.wait_for_selector(".result-content-container", timeout=15000)
        logger.info("✅ Result page successfully loaded.")
    except Exception:
        logger.warning("⚠️ Results container not detected.")

def wait_for_element_visible(page, locator_name, timeout=10000):
    """Waits for a specific element (by locator name) to be visible."""
    _, xpath = get_locator_path("global", locator_name)
    try:
        page.wait_for_selector(xpath, state="visible", timeout=timeout)
        logger.info("✅ Element '%s' is visible.", locator_name)
    except Exception as e:
        logger.error("❌ Timeout waiting for element '%s': %s", locator_name, e)

def vertical_scroll(page_obj, amount=500):
    """Scrolls the page by a specific pixel amount."""
    page_obj.mouse.wheel(0, int(amount))
    logger.info("📜 Scrolled down by %s pixels", amount)

def _smooth_scroll(page, total_pixels, steps=3, step_wait_ms=50):
    """Reduced steps and wait for fluent scrolling."""
    step = max(1, int(total_pixels / steps))
    for _ in range(steps):
        page.mouse.wheel(0, step)
        page.wait_for_timeout(step_wait_ms)

def scroll_until_text_visible(page, text, max_scrolls=None, scroll_wait=2):
    """Scrolls dynamically until exact text appears."""
    if max_scrolls is None:
        max_scrolls = _get_default_scroll_count()

    scrolls = 0
    target_text = str(text).strip('"').strip("'")
    logger.info(f"📜 Starting scroll-until for text: '{target_text}' (Max scrolls: {max_scrolls})")

    while scrolls < int(max_scrolls):
        locator = page.get_by_text(target_text, exact=True)
        if locator.count() > 0 and locator.first.is_visible(timeout=500):
            logger.info(f"✅ Found EXACT text '{target_text}' at scroll {scrolls}")
            return True

        page.mouse.wheel(0, 500)
        scrolls += 1
        logger.info(f"Scrolling down... ({scrolls}/{max_scrolls})")

        if scroll_wait:
            page.wait_for_timeout(float(scroll_wait) * 1500)

    logger.error(f"❌ Failed: Text '{target_text}' not found after {max_scrolls} scrolls.")
    return False


# --- VERIFICATIONS & VISUAL REGRESSION ---

def verify_exact_text(page, text: str, scroll_count=None):
    """DSL command: verify text "<text>" """
    logger.info("🔎 Verifying text: %s", text)
    universal_verify(page=page, command="visible", value=text, target="global", scroll_count=scroll_count)
    return True

def universal_verify(page, command, value=None, target="anywhere", scroll_count=None, stop_override=None):
    """Standardized NLP Verification Handler with fluent scrolling logic."""
    is_neg = any(word in command.lower() for word in ["not", "hidden", "disabled"])
    verb = command.lower().replace("not ", "").replace("is ", "").strip()
    
    values = value if isinstance(value, list) else [value]
    max_scroll_limit = int(scroll_count) if scroll_count is not None else _get_default_scroll_count()

    def _resolve_locator(val):
        if target and target not in ["anywhere", "global", "on page"]:
            _, xpath = get_locator_path("global", target)
            return page.locator(xpath).first
        return page.get_by_text(str(val), exact=True).first if val else None

    def _attempt_verify(val):
        loc = _resolve_locator(val)
        if loc is None:
            return False
        try:
            if verb in ["present", "visible", "on page"]:
                if is_neg:
                    expect(loc).not_to_be_visible()
                else:
                    expect(loc).to_be_visible()
            return True
        except Exception:
            return False

    last_scroll_top = page.evaluate("() => window.scrollY")
    scrolls_done = 0

    while scrolls_done <= max_scroll_limit:
        for val in values:
            if _attempt_verify(val):
                logger.info(f"🔒 EXACT MATCH VERIFIED: {val}")
                return

        if scrolls_done < max_scroll_limit:
            _smooth_scroll(page, 600)
            scrolls_done += 1
            try:
                page.wait_for_function("(prev) => window.scrollY > prev", arg=last_scroll_top, timeout=1000)
            except Exception:
                pass
            last_scroll_top = page.evaluate("() => window.scrollY")
        else:
            break

    raise Exception(f"Verify failed after {scrolls_done} scrolls: {value}")

def verify_image(page, image_path: str):
    """DSL command: verify image 'file.png'"""
    return verify_image_on_page(page, image_path)

def verify_image_on_page(page, image_path, threshold=0.9):
    """Industrial Standard Image Matching using OpenCV SSIM score."""
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Baseline image not found: {image_path}")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        page.screenshot(path=tmp.name, full_page=True)
        screenshot = cv2.imread(tmp.name)
        os.unlink(tmp.name)

    template = cv2.imread(image_path)
    s_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
    t_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(s_gray, t_gray, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)

    if max_val >= threshold:
        h, w = t_gray.shape
        crop = s_gray[max_loc[1] : max_loc[1] + h, max_loc[0] : max_loc[0] + w]
        if crop.shape == t_gray.shape:
            score = ssim(crop, t_gray)
            logger.info(f"🖼️ Visual Match: {score:.2%}")
            return score >= threshold
    return False

def verify_image_phash(page, image_path, max_diff=5):
    """Alternative Perceptual Hashing image verification."""
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Baseline image not found: {image_path}")
        
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        page.screenshot(path=tmp.name, full_page=True)
        screenshot = Image.open(tmp.name)
        os.unlink(tmp.name)

    baseline = Image.open(image_path)
    hash1 = imagehash.phash(screenshot)
    hash2 = imagehash.phash(baseline)

    diff = abs(hash1 - hash2)
    logger.info(f"🖼️ Perceptual hash difference: {diff}")
    return diff <= max_diff

def take_screenshot(page_obj, label="capture"):
    """Captures a full-page screenshot."""
    _ensure_dir("screenshots")
    filename = f"screenshots/{label}_{_timestamp()}.png"
    page_obj.screenshot(path=filename, full_page=True)
    logger.info("📸 Screenshot Saved: %s", filename)

# --- INTERNAL HELPERS ---

def _get_standard_timeout_ms():
    cfg = load_playwright_config()
    return int(cfg.get("use", {}).get("actionTimeout", 15000))

def _get_default_scroll_count():
    cfg = load_playwright_config()
    try:
        return int(cfg.get("run", {}).get("default_scroll_count", default_scroll_limit))
    except Exception:
        return default_scroll_limit
def fill_element(page, text, locator_name):
    """
    Executes a text fill. Features JS-injection fallback and ML self-healing.
    """
    primary_xpath, dna = get_locator_and_dna(locator_name)
    
    if not primary_xpath:
        raise Exception(f"Locator '{locator_name}' not found in any page.")

    def execute_robust_fill(xpath):
        """Helper to attempt standard fill, falling back to JS forced fill if blocked."""
        loc = page.locator(xpath).first
        try:
            # 1. Standard Human Fill
            loc.fill(text, timeout=3000)
            return True
        except PlaywrightTimeoutError:
            # 2. The JavaScript Bypass
            # If a modal is blocking the input, we use JS to forcibly inject the value into the DOM
            if loc.count() > 0:
                logger.warning(f"🛡️ Input field blocked. Forcing value via JavaScript...")
                loc.evaluate(f"el => el.value = '{text}'")
                # Trigger an 'input' event so modern front-end frameworks (React/Vue) notice the change
                loc.dispatch_event("input") 
                return True
            raise # Element truly missing, trigger ML Healer

    try:
        logger.info(f"⌨️ Attempting to type '{text}' into: {locator_name}")
        execute_robust_fill(primary_xpath)
        logger.info(f"✅ Fill successful.")

    except (PlaywrightTimeoutError, PlaywrightError) as play_err:
        logger.warning(f"⚠️ Primary input failed ({type(play_err).__name__}). Triggering ML Healer...")
        
        if not dna:
            raise Exception(f"Element broken and no ML DNA available to heal: {locator_name}")

        try:
            healed_xpath = ml_heal_element(page, dna)
        except Exception as ml_err:
            raise Exception(f"Self-healing math failed: {ml_err}")
        
        if healed_xpath:
            logger.info(f"✨ ML generated fallback XPath: {healed_xpath}")
            try:
                execute_robust_fill(healed_xpath)
                logger.info(f"Successfully healed and filled '{locator_name}'!")
            except Exception as retry_err:
                raise Exception(f"Both primary and ML-healed fills failed for: {locator_name}")
        else:
            raise Exception(f"Self-healing failed for: {locator_name}")    
@codeless_snippet("Refresh Page")
def refresh_current_page(page):
    page.reload()

@codeless_snippet("Click Element")
def click_action(page, locator):
    page.locator(locator).click()        