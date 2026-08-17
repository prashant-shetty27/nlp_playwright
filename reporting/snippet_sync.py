"""
reporting/snippet_sync.py
VS Code snippet generator — merged from sync_snippets.py + clean_snippet_files.py.
Harvests all locator names from both databases and writes automation-snippets.code-snippets.
"""
import glob
import json
import logging
import os
import re

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


# Slot → VS Code placeholder. {locator} becomes a dropdown of every saved
# locator; the rest become plain tab-stops with a sensible default.
_SLOT_DEFAULTS = {"text": "value", "number": "3", "variable": "my_var"}


def _covered_actions(snippets: dict) -> set:
    """Command types already reachable by typing a hand-written snippet."""
    from nlp.parser import parse_step

    covered = set()
    for spec in snippets.values():
        stmt = " ".join(spec.get("body", []))
        stmt = re.sub(r"\$\{\d+\|([^|}]*)\|\}", lambda m: m.group(1).split(",")[0], stmt)
        stmt = re.sub(r"\$\{\d+:([^}]*)\}", lambda m: m.group(1) or "x", stmt)
        stmt = re.sub(r"\$\{\d+\}", "x", stmt)
        try:
            covered.add(parse_step(stmt.strip()).type)
        except Exception:
            continue          # a body that no longer parses simply covers nothing
    return covered


def _snippets_from_templates(choice_list: str, already: dict) -> dict:
    """
    Build VS Code snippets from the templates in nlp/keywords.py.

    Only for actions no hand-written snippet already reaches, so nothing the
    operator uses today is renamed, replaced or shadowed.
    """
    from nlp.keywords import KEYWORD_MAP

    covered = _covered_actions(already)
    out: dict = {}
    for key, entry in KEYWORD_MAP.items():
        template = entry.get("template")
        action = entry.get("action", "")
        if not template or entry.get("deprecated") or action in covered:
            continue

        body, tab = template, 0

        def _next(_m, _slot):
            nonlocal tab
            tab += 1
            if _slot == "locator":
                return "${%d|%s|}" % (tab, choice_list)
            return "${%d:%s}" % (tab, _SLOT_DEFAULTS.get(_slot, "value"))

        for slot in re.findall(r"\{(\w+)\}", template):
            body = body.replace("{%s}" % slot, _next(None, slot), 1)

        out[f"NLP: {key.replace('_', ' ').title()}"] = {
            "prefix": entry["phrases"][0],
            "body": [body],
            "description": f"{action} — generated from nlp/keywords.py template",
        }
        covered.add(action)
    return out


def sync_locators_to_snippets() -> None:
    """Main orchestrator: generates the VS Code snippets file from all locator databases."""
    snippets_path = get_snippets_path()
    choice_list, total_count = harvest_locator_names()
    snippets: dict = {}

    # --- 1. STATIC WAIT KEYWORDS ---
    # The parser accepts only `wait N seconds`; "sleep"/"pause"/"force wait" are
    # not keywords. The familiar prefixes are kept, but every body now emits the
    # one form that actually runs.
    for prefix, label in {"wait": "Static Wait", "sleep": "Sleep", "pause": "Pause", "force wait": "Force Wait"}.items():
        snippets[f"Wait: {label}"] = {
            "prefix": prefix,
            "body": ["wait ${1:3} seconds"],
            "description": f"Static pause ({label}) — emits `wait N seconds`",
        }

    # --- 2. SCROLLING ---
    snippets["Action: Scroll Page"] = {
        "prefix": "scroll page",
        "body": ["scroll ${1|down,up|} ${2:500}"],
        "description": "Scrolls the viewport vertically by a pixel amount.",
    }
    snippets["Action: Scroll to Element"] = {
        "prefix": "scroll to",
        "body": [f"scroll to element ${{1|{choice_list}|}}"],
        "description": "Scrolls until element is in viewport center.",
    }
    # The web runner has no element-based scroll-until — only the text variant.
    # The prefix is kept, but the body emits the form that actually runs.
    snippets["Scroll: Until Element Visible"] = {
        "prefix": "scroll until element visible",
        "body": ['scroll until text "${1:visible text}" visible'],
        "description": "Looping scroll until text appears (no element variant exists on web).",
    }
    snippets["Scroll: Until Text Visible"] = {
        "prefix": "scroll until text visible",
        "body": ["scroll until text \"${1:text}\" visible, scroll count ${2:5}, scroll wait ${3:1}"],
        "description": "Looping scroll until specific text appears.",  # text-based: the web runner has no element variant
    }
    snippets["Action: Scroll to End"] = {
        "prefix": "scroll to end",
        "body": ["scroll to ${1|bottom,top|}"],
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
    # ONLY the states nlp/parser.py actually accepts. This list previously offered
    # sixteen; thirteen of them produced statements the parser rejects, so picking
    # one from the dropdown produced a step that failed at run time.
    verify_states = "visible,not visible,not present"
    snippets["Wait: Element State"] = {
        "prefix": "wait for element",
        "body": [f"wait until element ${{1|{choice_list}|}} is visible"],
        "description": "Smart wait for a specific element state.",
    }
    # Only the RESULT page has a dispatchable wait. The home/details variants were
    # suggesting a command that has never existed on either runner, so all three
    # prefixes now emit the one real form.
    for key in ("result", "home", "details"):
        snippets[f"Wait: {key.title()} Page Load"] = {
            "prefix": f"wait {key}",
            "body": ["wait for result page load"],
            "description": ("Wait for the result page to settle"
                            if key == "result" else
                            f"No '{key} page' wait exists — emits the result-page wait"),
        }

    # --- 5. CORE ACTIONS ---
    snippets.update({
        "Action: Refresh Page": {"prefix": "refresh", "body": ["refresh page"], "description": "Reload and wait for DOM."},
        "Action: Click": {"prefix": "click", "body": [f"click on element ${{1|{choice_list}|}}"], "description": "Click a saved locator."},
        "Verify: Text on Page": {
            "prefix": "verify on page",
            "body": ['verify texts ["${1:first}", "${2:second}"] on page'],
            "description": "Check multiple texts with optional scrolling",
        },
        "Action: Type Text": {"prefix": "type", "body": [f"type \"${{1:text}}\" into ${{2|{choice_list}|}}"], "description": "Types text into an input field."},
        "Action: Fill Text": {"prefix": "fill", "body": [f"fill \"${{1:text}}\" into ${{2|{choice_list}|}}"], "description": "Fills text into an input field."},
        "Verify: Element State": {"prefix": "verify element state", "body": [f"verify element ${{1|{choice_list}|}} is ${{2|{verify_states}|}}"], "description": "Assert functional or visual state."},
        "Action: Search": {"prefix": "search", "body": ["search for ${1:term}"], "description": "Perform a search."},
        "Action: Screenshot": {"prefix": "take screenshot", "body": [f"take screenshot of ${{1|{choice_list},viewport|}} as \"${{2:filename.png}}\""], "description": "Capture element or full page."},
    })

    # --- 7. JAVASCRIPT ACTIONS ---
    snippets.update({
        "JS: Click (bypass overlay)": {
            "prefix": "js click",
            "body": [f"js click ${{1|{choice_list}|}}"],
            "description": "JS el.click() — bypasses pointer-events:none / overlays",
        },
        "JS: Scroll To Element": {
            "prefix": "js scroll to",
            "body": [f"js scroll to ${{1|{choice_list}|}}"],
            "description": "scrollIntoView(smooth) via JavaScript",
        },
        "JS: Scroll Down": {
            "prefix": "js scroll down",
            "body": ["js scroll down ${1:300}"],
            "description": "window.scrollBy(0, N) — scroll down by N pixels",
        },
        "JS: Scroll Up": {
            "prefix": "js scroll up",
            "body": ["js scroll up ${1:300}"],
            "description": "window.scrollBy(0, -N) — scroll up by N pixels",
        },
        "JS: Scroll Top": {
            "prefix": "js scroll top",
            "body": ["js scroll top"],
            "description": "window.scrollTo(0,0) — jump to very top",
        },
        "JS: Scroll Bottom": {
            "prefix": "js scroll bottom",
            "body": ["js scroll bottom"],
            "description": "window.scrollTo(0, document.body.scrollHeight)",
        },
        "JS: Type (React-aware)": {
            "prefix": "js type",
            "body": [f"js type \"${{1:text}}\" into ${{2|{choice_list}|}}"],
            "description": "Native value setter + input/change events — works with React",
        },
        "JS: Focus": {
            "prefix": "js focus",
            "body": [f"js focus ${{1|{choice_list}|}}"],
            "description": "el.focus() + focus/focusin events",
        },
        "JS: Submit Form": {
            "prefix": "js submit",
            "body": [f"js submit ${{1|{choice_list}|}}"],
            "description": "el.submit() or dispatches submit event",
        },
        "JS: Dispatch Event": {
            "prefix": "js dispatch",
            "body": [f"js dispatch ${{1|change,click,input,submit,blur,focus|}} on ${{2|{choice_list}|}}"],
            "description": "dispatchEvent(new Event(name)) on any element",
        },
    })

    # --- 8. FAKER / TEST DATA ---
    for kind in ["name", "email", "phone", "uuid", "number", "address", "company", "username", "password"]:
        snippets[f"Data: Fake {kind.title()}"] = {
            "prefix": f"fake {kind}",
            "body": [f"generate fake {kind} as ${{1:{kind}_val}}"],
            "description": f"Store a fake {kind} into a variable",
        }
    snippets["Data: Random Number"] = {
        "prefix": "random number",
        "body": ["generate random number ${1:1} ${2:9999} as ${3:rand_num}"],
        "description": "Random integer between min and max stored as variable",
    }
    snippets["Data: Random String"] = {
        "prefix": "random string",
        "body": ["generate random string ${1:8} as ${2:rand_str}"],
        "description": "Random alphanumeric string of given length",
    }

    # --- 9. DATE / TIME ---
    snippets["Date: Get Today"] = {
        "prefix": "get today",
        "body": ["get today as ${1:today_date}"],
        "description": "Store today's date as a variable",
    }
    snippets["Date: Get Timestamp"] = {
        "prefix": "get timestamp",
        "body": ["get timestamp as ${1:ts}"],
        "description": "Store current datetime as a variable",
    }
    snippets["Date: Get Offset"] = {
        "prefix": "get date offset",
        "body": ["get date ${1:+7} days as ${2:future_date}"],
        "description": "Store a date N days from today (+N or -N)",
    }

    # --- 10. HTTP / API ---
    snippets["API: GET Request"] = {
        "prefix": "api get",
        "body": ["api get \"${1:https://api.example.com/endpoint}\" as ${2:api_response}"],
        "description": "HTTP GET and store JSON response",
    }
    snippets["API: POST Request"] = {
        "prefix": "api post",
        "body": ["api post \"${1:https://api.example.com/endpoint}\" with body '{\"key\": \"value\"}' as ${2:api_response}"],
        "description": "HTTP POST with body and store JSON response",
    }
    snippets["API: Store JSON Path"] = {
        "prefix": "store json path",
        "body": ["store json ${1:api_response} path ${2:data.0.id} as ${3:extracted_val}"],
        "description": "Extract a value from a stored JSON response using dot-path",
    }

    # --- 11. EXCEL / CSV ---
    snippets["Excel: Read Cell"] = {
        "prefix": "read excel cell",
        "body": ["read excel \"${1:data/test_data.xlsx}\" row ${2:1} col ${3:1} as ${4:cell_val}"],
        "description": "Read a single cell from an Excel file",
    }
    snippets["Excel: Read Row"] = {
        "prefix": "read excel row",
        "body": ["read excel \"${1:data/test_data.xlsx}\" row ${2:1} as ${3:row_data}"],
        "description": "Read an entire row from an Excel file as a list",
    }
    snippets["CSV: Read Cell"] = {
        "prefix": "read csv cell",
        "body": ["read csv \"${1:data/test_data.csv}\" row ${2:1} col ${3:1} as ${4:csv_val}"],
        "description": "Read a single cell from a CSV file",
    }

    # --- 5b. GENERATED FROM KEYWORD_MAP TEMPLATES ---
    # Everything above is hand-written and stays that way, so existing prefixes
    # keep working. This step fills the gap: any command that has a template in
    # nlp/keywords.py but is not already reachable from a hand-written snippet
    # gets one generated for it. Adding a command therefore means editing
    # KEYWORD_MAP only — the VS Code surface follows automatically, and
    # tests/test_suggestions.py fails if a template stops parsing.
    snippets.update(_snippets_from_templates(choice_list, already=snippets))

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
