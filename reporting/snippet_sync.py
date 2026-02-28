"""
reporting/snippet_sync.py
VS Code snippet generator — merged from sync_snippets.py + clean_snippet_files.py.
Harvests all locator names from both databases and writes automation-snippets.code-snippets.
"""
import glob
import json
import logging
import os

from config import settings

logger = logging.getLogger(__name__)

SNIPPETS_FILE_NAME = "automation-snippets.code-snippets"


def get_snippets_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, "Library/Application Support/Code/User/snippets", SNIPPETS_FILE_NAME)


def harvest_locator_names() -> tuple[str, int]:
    """
    Reads both manual and ML databases and returns a comma-separated choice list
    for VS Code snippet dropdowns, plus a total count.
    """
    all_names: set = set()
    manual_count = 0
    recorded_count = 0

    if os.path.exists(settings.MANUAL_LOCATORS_FILE):
        try:
            with open(settings.MANUAL_LOCATORS_FILE, "r") as f:
                data = json.load(f)
            if data:
                keys = {key for page in data.values() for key in page.keys()}
                all_names.update(keys)
                manual_count = len(keys)
        except Exception as e:
            logger.warning("⚠️ Could not load manual locators: %s", e)

    if os.path.exists(settings.RECORDED_ELEMENTS_FILE):
        try:
            with open(settings.RECORDED_ELEMENTS_FILE, "r") as f:
                data = json.load(f)
            if data:
                keys = {key for page in data.values() for key in page.keys()}
                all_names.update(keys)
                recorded_count = len(keys)
        except Exception as e:
            logger.warning("⚠️ Could not load recorded elements: %s", e)

    print("\n" + "=" * 35)
    print("📊 DATABASE HARVEST REPORT")
    print(f"Manual Locators   : {manual_count}")
    print(f"Recorded Locators : {recorded_count}")
    print("=" * 35)

    if not all_names:
        return "anywhere", 0

    choice_list = ",".join(sorted(all_names))
    return choice_list, len(all_names)


def sync_locators_to_snippets() -> None:
    """Main orchestrator: generates the VS Code snippets file from all locator databases."""
    snippets_path = get_snippets_path()
    choice_list, total_count = harvest_locator_names()
    snippets: dict = {}

    # --- 1. STATIC WAIT KEYWORDS ---
    for prefix, label in {"wait": "Static Wait", "sleep": "Sleep", "pause": "Pause", "force wait": "Force Wait"}.items():
        snippets[f"Wait: {label}"] = {
            "prefix": prefix,
            "body": [f"{prefix} ${{1:seconds}} seconds"],
            "description": f"Static pause using {label}",
        }

    # --- 2. SCROLLING ---
    snippets["Action: Scroll Page"] = {
        "prefix": "scroll page",
        "body": ["scroll page ${1|up,down|} by ${2:500} pixels"],
        "description": "Scrolls the viewport vertically by a pixel amount.",
    }
    snippets["Action: Scroll to Element"] = {
        "prefix": "scroll to",
        "body": [f"scroll to element ${{1|{choice_list}|}}"],
        "description": "Scrolls until element is in viewport center.",
    }
    snippets["Scroll: Until Element Visible"] = {
        "prefix": "scroll until element visible",
        "body": [f"scroll until element ${{1|{choice_list}|}} visible, scroll count ${{2:5}}, scroll wait ${{3:1}}"],
        "description": "Looping scroll until element is found.",
    }
    snippets["Scroll: Until Text Visible"] = {
        "prefix": "scroll until text visible",
        "body": ["scroll until text \"${1:text}\" visible, scroll count ${2:5}, scroll wait ${3:1}"],
        "description": "Looping scroll until specific text appears.",
    }
    snippets["Action: Scroll to End"] = {
        "prefix": "scroll to end",
        "body": ["scroll to ${1|top,bottom|} of page"],
        "description": "Instantly scrolls to top or bottom.",
    }

    # --- 3. VISUAL REGRESSION ---
    thresholds = "50%,70%,90%,100%"
    snippets["Verify: Visual Image"] = {
        "prefix": "verify image",
        "body": [f"verify image \"${{1:filename.png}}\" matches element ${{2|{choice_list},viewport|}} with threshold ${{3|{thresholds}|}}"],
        "description": "Element visual match against a baseline image.",
    }
    snippets["Verify: Image On Page"] = {
        "prefix": "verify image on page",
        "body": [f"verify image \"${{1:filename.png}}\" on page with threshold ${{2|{thresholds}|}}"],
        "description": "Checks if image exists anywhere on the visible screen.",
    }

    # --- 4. WAITS & VERIFICATIONS ---
    verify_states = (
        "visible,hidden,present,not present,displayed,not visible,"
        "enabled,disabled,editable,clickable,selected,not selected,"
        "empty,not empty,focused,not focused"
    )
    snippets["Wait: Element State"] = {
        "prefix": "wait for element",
        "body": [f"wait for element ${{1|{choice_list}|}} to be ${{2|{verify_states}|}} (timeout 10s)"],
        "description": "Smart wait for a specific element state.",
    }
    for key, label in {"home": "home page", "result": "result page", "details": "details page"}.items():
        snippets[f"Wait: {label.title()} Load"] = {
            "prefix": f"wait {key}",
            "body": [f"wait for {label} to load (network idle, max 10s)"],
            "description": f"Wait for {label} network idle",
        }

    # --- 5. CORE ACTIONS ---
    snippets.update({
        "Action: Refresh Page": {"prefix": "refresh", "body": ["refresh page"], "description": "Reload and wait for DOM."},
        "Action: Click": {"prefix": "click", "body": [f"click on element ${{1|{choice_list}|}}"], "description": "Click a saved locator."},
        "Verify: Text on Page": {
            "prefix": "verify on page",
            "body": ["verify [${1:text1}, ${2:text2}] is ${3|present,not present,visible,hidden|} on page, scroll ${4:count}, stop ${5|true,false|}"],
            "description": "Check multiple texts with optional scrolling",
        },
        "Action: Type Text": {"prefix": "type", "body": [f"type \"${{1:text}}\" into ${{2|{choice_list}|}}"], "description": "Types text into an input field."},
        "Action: Fill Text": {"prefix": "fill", "body": [f"fill \"${{1:text}}\" into ${{2|{choice_list}|}}"], "description": "Fills text into an input field."},
        "Verify: Element State": {"prefix": "verify element state", "body": [f"verify element ${{1|{choice_list}|}} is ${{2|{verify_states}|}}"], "description": "Assert functional or visual state."},
        "Action: Search": {"prefix": "search", "body": ["search for ${1:term}"], "description": "Perform a search."},
        "Action: Screenshot": {"prefix": "take screenshot", "body": [f"take screenshot of ${{1|{choice_list},viewport|}} as \"${{2:filename.png}}\""], "description": "Capture element or full page."},
    })

    # --- 6. WRITE ---
    try:
        os.makedirs(os.path.dirname(snippets_path), exist_ok=True)
        with open(snippets_path, "w") as f:
            json.dump(snippets, f, indent=2)
        print("\n" + "=" * 35)
        print("      SYNC REPORT GENERATED")
        print("=" * 35)
        print(f"✅ Target : {snippets_path}")
        print(f"✅ Locators: {total_count}")
        print(f"✅ Snippets: {len(snippets)}")
        print("=" * 35)
    except Exception as e:
        logger.error("❌ Failed to write snippet file: %s", e)


def clean_old_snippet_files() -> None:
    """
    Removes all .code-snippets files except automation-snippets.code-snippets.
    Merged from clean_snippet_files.py.
    """
    snippet_dir = os.path.expanduser("~/Library/Application Support/Code/User/snippets")
    files = glob.glob(os.path.join(snippet_dir, "*.code-snippets"))
    for f in files:
        if not f.endswith(SNIPPETS_FILE_NAME):
            print(f"Deleting: {f}")
            os.remove(f)
    print(f"✅ Only {SNIPPETS_FILE_NAME} kept.")


if __name__ == "__main__":
    sync_locators_to_snippets()
