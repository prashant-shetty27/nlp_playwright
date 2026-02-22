import json
import os

# --- Configuration Constants ---
LOCATORS_FILE = "locators_manual.json"  
RECORDED_FILE = "recorded_elements.json" 
SNIPPETS_FILE_NAME = "automation-snippets.code-snippets"

def get_snippets_path():
    home = os.path.expanduser("~")
    return os.path.join(home, "Library/Application Support/Code/User/snippets", SNIPPETS_FILE_NAME)

def load_locators():
    """
    Harvests locators from both manual and ML databases to populate VS Code dropdowns.
    """
    all_names = set()
    manual_count = 0
    recorded_count = 0

    # 1. Harvest from the old manual database
    if os.path.exists(LOCATORS_FILE):
        try:
            with open(LOCATORS_FILE, "r") as f:
                loc_data = json.load(f)
                if loc_data:
                    keys = {key for page in loc_data.values() for key in page.keys()}
                    all_names.update(keys)
                    manual_count = len(keys)
        except Exception as e:
            print(f"⚠️ Could not load {LOCATORS_FILE}: {e}")

    # 2. Harvest from the new ML Spy database
    if os.path.exists(RECORDED_FILE):
        try:
            with open(RECORDED_FILE, "r") as f:
                rec_data = json.load(f)
                if rec_data:
                    keys = {key for page in rec_data.values() for key in page.keys()}
                    all_names.update(keys)
                    recorded_count = len(keys)
        except Exception as e:
            print(f"⚠️ Could not load {RECORDED_FILE}: {e}")

    # --- TELEMETRY OUTPUT ---
    print("\n" + "="*35)
    print(f"📊 DATABASE HARVEST REPORT")
    print(f"Manual Locators   : {manual_count}")
    print(f"Recorded Locators : {recorded_count}")
    print("="*35)

    if not all_names:
        return "anywhere", 0
        
    choice_list = ",".join(sorted(list(all_names)))
    return choice_list, len(all_names)

def sync_locators_to_snippets():
    """
    Main Orchestrator for generating the VS Code Snippets file.
    Architectural Impact: Consolidates Visual, Functional, and Scroll logic.
    """
    snippets_path = get_snippets_path()
    choice_list, all_names_count = load_locators()
    snippets = {}

    # --- 1. STATIC WAIT KEYWORDS ---
    wait_variants = {
        "wait": "Static Wait",
        "sleep": "Sleep",
        "pause": "Pause",
        "force wait": "Force Wait"
    }
    for prefix, label in wait_variants.items():
        snippets[f"Wait: {label}"] = {
            "prefix": prefix,
            "body": [f"{prefix} ${{1:seconds}} seconds"],
            "description": f"Static Pause execution using {label}"
        }

    # --- 2. ADVANCED SCROLLING FUNCTIONS ---
    snippets["Action: Scroll Page"] = {
        "prefix": "scroll page",
        "body": ["scroll page ${1|up,down|} by ${2:500} pixels"],
        "description": "Scrolls the viewport vertically by a specific pixel amount."
    }

    snippets["Action: Scroll to Element"] = {
        "prefix": "scroll to",
        "body": [f"scroll to element ${{1|{choice_list}|}}"],
        "description": "Scrolls until element is in viewport center."
    }

    snippets["Scroll: Until Element Visible"] = {
        "prefix": "scroll until element visible",
        "body": [
            f"scroll until element ${{1|{choice_list}|}} visible, scroll count ${{2:5}}, scroll wait ${{3:1}}"
        ],
        "description": "Looping scroll until element is found. Set max attempts and pause."
    }

    snippets["Scroll: Until Text Visible"] = {
        "prefix": "scroll until text visible",
        "body": [
            "scroll until text \"${1:text}\" visible, scroll count ${2:5}, scroll wait ${3:1}"
        ],
        "description": "Looping scroll until specific text appears. Set max attempts and pause."
    }

    snippets["Action: Scroll to End"] = {
        "prefix": "scroll to end",
        "body": ["scroll to ${1|top,bottom|} of page"],
        "description": "Instantly scrolls to the absolute top or bottom of the document."
    }

    # --- 3. VISUAL REGRESSION (IMAGE MATCHING) ---
    thresholds = "50%,70%,90%,100%"
    snippets["Verify: Visual Image"] = {
        "prefix": "verify image",
        "body": [
            f"verify image \"${{1:filename.png}}\" matches element ${{2|{choice_list},viewport|}} with threshold ${{3|{thresholds}|}}"
        ],
        "description": "--- ELEMENT VISUAL MATCH ---\n1. Upload baseline to './upload'.\n2. Checks specific element match."
    }

    snippets["Verify: Image On Page"] = {
        "prefix": "verify image on page",
        "body": [
            f"verify image \"${{1:filename.png}}\" on page with threshold ${{2|{thresholds}|}}"
        ],
        "description": "--- PAGE VISUAL MATCH ---\nChecks if the image exists anywhere on the current visible screen."
    }

    # --- 4. SMART WAITS & VERIFICATIONS ---
    verify_states = (
        "visible,hidden,present,not present,displayed,not visible,"
        "enabled,disabled,editable,clickable,selected,not selected,"
        "empty,not empty,focused,not focused"
    )

    snippets["Wait: Element State"] = {
        "prefix": "wait for element",
        "body": [f"wait for element ${{1|{choice_list}|}} to be ${{2|{verify_states}|}} (timeout 10s)"],
        "description": "Smart wait for a specific functional or visual state (Actionability Check)."
    }

    # --- 5. PAGE LOAD WAITS ---
    page_loads = {"home": "home page", "result": "result page", "details": "details page"}
    for key, label in page_loads.items():
        snippets[f"Wait: {label.title()} Load"] = {
            "prefix": f"wait {key}",
            "body": [f"wait for {label} to load (network idle, max 10s)"],
            "description": f"Wait for {label} network idle or DOM load"
        }

    # --- 6. CORE ACTIONS & VERIFICATIONS ---
    snippets.update({
        "Action: Refresh Page": {
            "prefix": "refresh",
            "body": ["refresh page"],
            "description": "Reloads the current page and waits for the DOM to settle."
        },
        "Action: Click": {
            "prefix": "click",
            "body": [f"click on element ${{1|{choice_list}|}}"],
            "description": "Click a saved locator from memory"
        },
        "Verify: Text on Page": {
            "prefix": "verify on page",
            "body": [
                "verify [${1:text1}, ${2:text2}] is ${3|present,not present,visible,hidden|} on page, scroll ${4:count}, stop ${5|true,false|}"
            ],
            "description": "Check multiple texts with optional scrolling"
        },
        "Action: Type Text": {
            "prefix": "type",
            "body": [f"type \"${{1:text}}\" into ${{2|{choice_list}|}}"],
            "description": "Types text into an input field (Includes ML Healing & JS Bypass)."
        },
        "Action: Fill Text": {
            "prefix": "fill",
            "body": [f"fill \"${{1:text}}\" into ${{2|{choice_list}|}}"],
            "description": "Fills text into an input field (Alias for Type)."
        },
        "Verify: Element State": {
            "prefix": "verify element state",
            "body": [f"verify element ${{1|{choice_list}|}} is ${{2|{verify_states}|}}"],
            "description": "Assert the functional or visual state of an element."
        },
        "Action: Search": {
            "prefix": "search",
            "body": ["search for ${1:term}"],
            "description": "Perform a Justdial search"
        },
        "Action: Screenshot": {
            "prefix": "take screenshot",
            "body": [f"take screenshot of ${{1|{choice_list},viewport|}} as \"${{2:filename.png}}\""],
            "description": "Capture element or full page and save to project folder."
        }
    })

    # --- 7. SAFE WRITE OPERATION ---
    try:
        os.makedirs(os.path.dirname(snippets_path), exist_ok=True)
        with open(snippets_path, "w") as f:
            json.dump(snippets, f, indent=2)
            
        print("\n" + "="*35)
        print("      SYNC REPORT GENERATED")
        print("="*35)
        print(f"✅ Target Path: {snippets_path}")
        print(f"✅ Locators Found: {all_names_count}")
        print(f"✅ Snippets Created: {len(snippets)}")
        print(f"👉 Source File: {LOCATORS_FILE}")
        print("="*35)
    except Exception as e:
        print(f"❌ Failed to write snippet file: {e}")

if __name__ == "__main__":
    sync_locators_to_snippets()