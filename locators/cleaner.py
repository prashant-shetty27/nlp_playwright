"""
locators/cleaner.py
Sanitizes the manual locator database — strips malformed keys/values.
Handles both web flat schema and Appium platform/screen/element nested schema.
Moved from clean_locators.py.
"""
import json
import logging
import os
import shutil
from datetime import datetime

from config import settings
from locators.io_utils import atomic_write_json, file_lock, read_json

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Top-level keys that use the nested platform → screen → element schema
_APPIUM_PLATFORM_KEYS = {"ios", "android", "hybrid"}


def _scrub(name: str) -> str:
    """Strip characters that corrupt JSON keys."""
    return name.split(":")[0].strip().replace("{", "").replace('"', '').replace(',', '')


def sanitize_database(file_path: str | None = None) -> None:
    """
    Reads locators_manual.json, scrubs malformed page/screen/element names,
    and writes the cleaned data back to disk.

    Handles two schemas:
      - Web/flat:   {page: {element: xpath_or_dict}}
      - Appium:     {platform: {screen: {element: locator_dict}}}
    """
    path = file_path or settings.MANUAL_LOCATORS_FILE

    if not os.path.exists(path):
        logger.error("File %s not found.", path)
        return

    try:
        with file_lock(path, exclusive=False):
            data = read_json(path, retries=2)
    except json.JSONDecodeError as e:
        logger.error("JSON is corrupted! Manual fix required: %s", e)
        return
    except OSError as e:
        logger.error("Failed to read %s: %s", path, e)
        return

    clean_data: dict = {}
    changes_made = False

    for page_name, elements in data.items():
        clean_page = _scrub(page_name)
        if clean_page != page_name:
            changes_made = True
            logger.info("🧹 Cleaned page/platform: '%s' → '%s'", page_name, clean_page)

        if clean_page not in clean_data:
            clean_data[clean_page] = {}

        if clean_page in _APPIUM_PLATFORM_KEYS:
            # ── Appium: platform → screen → element ───────────────────────────
            for screen_name, screen_elements in elements.items():
                clean_screen = _scrub(screen_name)
                if clean_screen != screen_name:
                    changes_made = True
                    logger.info("🧹 Cleaned screen: '%s' → '%s'", screen_name, clean_screen)

                if clean_screen not in clean_data[clean_page]:
                    clean_data[clean_page][clean_screen] = {}

                if isinstance(screen_elements, dict):
                    for element_name, locator in screen_elements.items():
                        clean_el = element_name.strip().replace('"', '').replace(',', '')
                        if clean_el != element_name:
                            changes_made = True
                            logger.info(
                                "🧹 Cleaned element [%s/%s]: '%s' → '%s'",
                                clean_page, clean_screen, element_name, clean_el
                            )
                        clean_data[clean_page][clean_screen][clean_el] = locator
        else:
            # ── Web/flat: page → element ──────────────────────────────────────
            for element_name, xpath in elements.items():
                clean_name = element_name.strip().replace('"', '').replace(',', '')
                if clean_name != element_name:
                    changes_made = True
                    logger.info("🧹 Cleaned element: '%s' → '%s'", element_name, clean_name)
                clean_data[clean_page][clean_name] = xpath

    if changes_made:
        # Keep a timestamped backup before mutating locator DB.
        backup_path = f"{path}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(path, backup_path)
            logger.info("🧾 Backup created: %s", backup_path)
        except OSError as e:
            logger.warning("⚠️ Could not create backup before sanitize write: %s", e)
        with file_lock(path, exclusive=True):
            atomic_write_json(path, clean_data, indent=2)
        logger.info("✨ Database sanitized and saved.")
    else:
        logger.info("✅ Database already clean. No changes needed.")


if __name__ == "__main__":
    sanitize_database()
