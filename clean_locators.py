import json
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FILE_NAME = "locators_manual.json"

def sanitize_database():
    if not os.path.exists(FILE_NAME):
        logger.error("File %s not found.", FILE_NAME)
        return

    with open(FILE_NAME, "r") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            logger.error("JSON is corrupted! Manual fix required: %s", e)
            return

    clean_data = {}
    changes_made = False

    for page_name, elements in data.items():
        # 💡 Logic: Scrub page names (remove "result_page": { debris)
        clean_page = page_name.split(":")[0].strip().replace("{", "").replace('"', '')
        
        if clean_page != page_name:
            changes_made = True
            logger.info("🧹 Cleaned page name: '%s' -> '%s'", page_name, clean_page)
        
        if clean_page not in clean_data:
            clean_data[clean_page] = {}

        for element_name, xpath in elements.items():
            # 💡 Logic: Scrub element names
            clean_name = element_name.strip().replace('"', '').replace(',', '')
            
            if clean_name != element_name:
                changes_made = True
                logger.info("🧹 Cleaned element: '%s' -> '%s'", element_name, clean_name)
            
            clean_data[clean_page][clean_name] = xpath

    if changes_made:
        with open(FILE_NAME, "w") as f:
            json.dump(clean_data, f, indent=2)
        logger.info("✨ Database successfully sanitized and saved.")
    else:
        logger.info("✅ Database is already clean. No changes needed.")

if __name__ == "__main__":
    sanitize_database()