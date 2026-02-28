"""
locators/cleaner.py
Sanitizes the manual locator database — strips malformed keys/values.
Moved from clean_locators.py.
"""
import json
import logging
import os

from config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def sanitize_database(file_path: str | None = None) -> None:
    """
    Reads locators_manual.json, scrubs malformed page/element names,
    and writes the cleaned data back to disk.
    """
    path = file_path or settings.MANUAL_LOCATORS_FILE

    if not os.path.exists(path):
        logger.error("File %s not found.", path)
        return

    with open(path, "r") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error("JSON is corrupted! Manual fix required: %s", e)
            return

    clean_data: dict = {}
    changes_made = False

    for page_name, elements in data.items():
        clean_page = page_name.split(":")[0].strip().replace("{", "").replace('"', '')
        if clean_page != page_name:
            changes_made = True
            logger.info("🧹 Cleaned page name: '%s' → '%s'", page_name, clean_page)

        if clean_page not in clean_data:
            clean_data[clean_page] = {}

        for element_name, xpath in elements.items():
            clean_name = element_name.strip().replace('"', '').replace(',', '')
            if clean_name != element_name:
                changes_made = True
                logger.info("🧹 Cleaned element: '%s' → '%s'", element_name, clean_name)
            clean_data[clean_page][clean_name] = xpath

    if changes_made:
        with open(path, "w") as f:
            json.dump(clean_data, f, indent=2)
        logger.info("✨ Database sanitized and saved.")
    else:
        logger.info("✅ Database already clean. No changes needed.")


if __name__ == "__main__":
    sanitize_database()
