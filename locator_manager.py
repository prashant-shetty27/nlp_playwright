import json
import logging
import os

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

MANUAL_LOCATORS_FILE = "locators_manual.json"
logger = logging.getLogger(__name__)
VALID_PAGES = ["home_page", "result_page", "b2b_page", "search_page"]
def load_locators():
    """
    Logic: Robust loading with error handling.
    Impact: Prevents a crash if the file is empty, missing, or corrupted.
    """
    if not os.path.exists(MANUAL_LOCATORS_FILE):
        logger.info("ℹ️ Memory file not found. Starting with empty database.")
        return {}

    try:
        with open(MANUAL_LOCATORS_FILE, "r") as f:
            data = json.load(f)
            # Ensure we always return a dictionary even if file is null
            return data if data is not None else {}
    except (json.JSONDecodeError, IOError) as e:
        logger.error("❌ CRITICAL: Could not read %s. Data may be corrupted: %s", MANUAL_LOCATORS_FILE, e)
        # Returning an empty dict allows the program to continue rather than crashing
        return {}

def save_locators(data):
    """
    Logic: Pure IO (Input/Output) function.
    Impact: Isolates the 'writing' task so it can be called by any management function.
    """
    try:
        with open(MANUAL_LOCATORS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        logger.error("❌ Failed to save locators to disk: %s", e)

def add_locator(page_name, name, locator):
    # --- STEP 1: SCRUB THE DATA (Full-Proofing) ---
    # Removes any accidental "{" or ":" or extra spaces from snippets
    clean_page = page_name.split(":")[0].strip().replace('"', '').replace('{', '')
    clean_name = name.strip().replace('"', '').replace(',', '')
    
    # --- STEP 2: LOAD CURRENT MEMORY ---
    locs = load_locators()

    if clean_page not in locs:
        locs[clean_page] = {}

    # --- STEP 3: DUPLICATE PREVENTION ---
    # Check if this exact XPath already exists under a different name
    for p_name, elements in locs.items():
        for e_name, e_path in elements.items():
            if e_path == locator:
                logger.warning("🚫 DATA INTEGRITY: This XPath is already registered as '%s' on '%s'", e_name, p_name)
                return False 

    # --- STEP 4: PERSISTENCE ---
    locs[clean_page][clean_name] = locator
    save_locators(locs) # Ensure this function writes the JSON to disk
    logger.info("✅ Locator '%s' successfully locked into '%s'", clean_name, clean_page)
    return True
def get_locator_path(page_name, locator_name):
    """
    Logic: Searches for a locator by name.
    Impact: This is the 'bridge' that allows actions.py to find XPaths.
    """
    locs = load_locators()
    
    # 1. Check specific page if provided
    if page_name in locs and locator_name in locs[page_name]:
        return page_name, locs[page_name][locator_name]
    
    # 2. Global Scan (Fallback)
    for p, elements in locs.items():
        if locator_name in elements:
            return p, elements[locator_name]
            
    raise ValueError(f"Locator '{locator_name}' not found in any page.")

def get_locator_and_dna(locator_name):
    """
    Master Dispatcher: Scans both the ML database and the Manual database.
    Returns: (xpath_string, element_dna_dict)
    """
    # 1. Look in the ML Database First
    if os.path.exists("recorded_elements.json"):
        try:
            with open("recorded_elements.json", "r") as f:
                ml_data = json.load(f)
                
            for page, elements in ml_data.items():
                if locator_name in elements:
                    dna = elements[locator_name]
                    # Robust fallback: Catches custom_xpath, your typo (custom_xpath_P), or old absolute paths
                    xpath = dna.get("custom_xpath") or dna.get("custom_xpath_P") or dna.get("absoluteXPath")
                    return xpath, dna
        except Exception as e:
            logger.error(f"❌ Error reading recorded_elements.json: {e}")

    # 2. Look in the Manual Database Second
    if os.path.exists("locators_manual.json"):
        try:
            with open("locators_manual.json", "r") as f:
                manual_data = json.load(f)
                
            for page, elements in manual_data.items():
                if locator_name in elements:
                    xpath = elements[locator_name]
                    
                    # Handle case where manual DB stores a string vs a dictionary
                    if isinstance(xpath, dict):
                        xpath = xpath.get("xpath") or xpath.get("value")
                        
                    # Manual locators do not have ML DNA, so we return None for the second variable
                    return xpath, None 
        except Exception as e:
            logger.error(f"❌ Error reading locators_manual.json: {e}")

    # 3. Complete Failure (Element doesn't exist anywhere)
    return None, None
class LocatorWatcher(FileSystemEventHandler):
    def __init__(self, json_file_path: str):
        self.file_path = json_file_path
        self.live_locators = {}
        self.load_into_memory()

    def load_into_memory(self):
        try:
            with open(self.file_path, 'r') as file:
                self.live_locators = json.load(file)
                # Here, you would emit a signal to your UI/Frontend to refresh the snippet dropdown
                print(f"[*] Reloaded locators from {self.file_path}")
        except Exception as e:
            print(f"Error loading locators: {e}")

    def on_modified(self, event):
        # Triggered automatically by the OS when the JSON file is saved
        if event.src_path.endswith(self.file_path):
            self.load_into_memory()

def start_watcher(path: str) -> LocatorWatcher:
    watcher = LocatorWatcher(path)
    observer = Observer()
    # Schedule the observer to watch the directory containing the file
    observer.schedule(watcher, path=path.rsplit('/', 1)[0] or '.', recursive=False)
    observer.start()
    return watcher