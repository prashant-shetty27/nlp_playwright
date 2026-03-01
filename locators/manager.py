"""
locators/manager.py
Dual-database locator management — extracted from locator_manager.py.
LocatorWatcher has been moved to locators/watcher.py.
"""
import json
import logging
import os

from config import settings

logger = logging.getLogger(__name__)

VALID_PAGES = ["home_page", "result_page", "b2b_page", "search_page"]


def load_locators() -> dict:
    """Robust loading with error handling. Returns {} on missing/corrupt file."""
    path = settings.MANUAL_LOCATORS_FILE
    if not os.path.exists(path):
        logger.info("ℹ️ Manual locators file not found. Starting with empty database.")
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if data is not None else {}
    except (json.JSONDecodeError, IOError) as e:
        logger.error("❌ CRITICAL: Could not read %s. Data may be corrupted: %s", path, e)
        return {}


def save_locators(data: dict) -> None:
    """Persists locator database to disk."""
    path = settings.MANUAL_LOCATORS_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        logger.error("❌ Failed to save locators to disk: %s", e)


def add_locator(page_name: str, name: str, locator: str) -> bool:
    """Adds a locator with duplicate-XPath prevention and data scrubbing."""
    clean_page = page_name.split(":")[0].strip().replace('"', '').replace('{', '')
    clean_name = name.strip().replace('"', '').replace(',', '')

    locs = load_locators()

    if clean_page not in locs:
        locs[clean_page] = {}

    # Duplicate XPath check across all pages
    for p_name, elements in locs.items():
        for e_name, e_path in elements.items():
            if e_path == locator:
                logger.warning(
                    "🚫 DATA INTEGRITY: XPath already registered as '%s' on '%s'", e_name, p_name
                )
                return False

    locs[clean_page][clean_name] = locator
    save_locators(locs)
    logger.info("✅ Locator '%s' locked into '%s'", clean_name, clean_page)
    return True


def get_locator_path(page_name: str, locator_name: str) -> tuple[str, str]:
    """Searches for a locator by name. Returns (page_name, xpath)."""
    locs = load_locators()

    if page_name in locs and locator_name in locs[page_name]:
        return page_name, locs[page_name][locator_name]

    for p, elements in locs.items():
        if locator_name in elements:
            return p, elements[locator_name]

    raise ValueError(f"Locator '{locator_name}' not found in any page.")


def get_locator_and_dna(locator_name: str) -> tuple:
    """
    Master dispatcher: scans both ML database and manual database.
    Returns: (xpath_string, element_dna_dict | None)
    """
    # 1. ML database first
    ml_path = settings.RECORDED_ELEMENTS_FILE
    if os.path.exists(ml_path):
        try:
            with open(ml_path, "r") as f:
                ml_data = json.load(f)
            for page, elements in ml_data.items():
                if locator_name in elements:
                    dna = elements[locator_name]
                    xpath = (
                        dna.get("custom_xpath")
                        or dna.get("custom_xpath_P")
                        or dna.get("absoluteXPath")
                    )
                    return xpath, dna
        except Exception as e:
            logger.error("❌ Error reading recorded_elements.json: %s", e)

    # 2. Manual database second
    manual_path = settings.MANUAL_LOCATORS_FILE
    if os.path.exists(manual_path):
        try:
            with open(manual_path, "r") as f:
                manual_data = json.load(f)
            for page, elements in manual_data.items():
                if locator_name in elements:
                    xpath = elements[locator_name]
                    if isinstance(xpath, dict):
                        xpath = xpath.get("xpath") or xpath.get("value")
                    return xpath, None
        except Exception as e:
            logger.error("❌ Error reading locators_manual.json: %s", e)

    return None, None


def get_all_locators() -> dict:
    """
    Loads and merges locator names from manual + recorded databases for UI dropdowns.
    Returns: {locator_name: "PAGE ➔ locator_name"}
    """
    locator_mapping: dict = {}

    # Manual locators
    manual_path = settings.MANUAL_LOCATORS_FILE
    if os.path.exists(manual_path):
        try:
            with open(manual_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for page_name, locators in (data or {}).items():
                if isinstance(locators, dict):
                    for element_name in locators.keys():
                        locator_mapping[element_name] = f"{str(page_name).upper()} ➔ {element_name}"
        except Exception as e:
            logger.warning("Could not read manual locator DB for dropdowns: %s", e)

    # Recorded/ML locators
    recorded_path = settings.RECORDED_ELEMENTS_FILE
    if os.path.exists(recorded_path):
        try:
            with open(recorded_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for page_name, locators in (data or {}).items():
                if isinstance(locators, dict):
                    for element_name in locators.keys():
                        locator_mapping[element_name] = f"{str(page_name).upper()} ➔ {element_name}"
        except Exception as e:
            logger.warning("Could not read recorded locator DB for dropdowns: %s", e)

    return locator_mapping
