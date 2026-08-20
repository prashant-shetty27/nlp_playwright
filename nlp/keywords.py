"""
File: nlp_keywords.py
Description: The NLP Keyword Map translates natural language phrases into
specific internal action triggers for the execution engine.

This map is the SINGLE SOURCE for step suggestions. Two surfaces read it:

  * POST /nlp/suggest  (api/routes/nlp.py) — autocomplete in the dashboard,
    which shows `phrases`.
  * reporting/snippet_sync.py — VS Code snippets, which are built from
    `template`.

Entry fields
------------
phrases     Human-readable hints shown while typing. Prose, not statements —
            they intentionally omit arguments.
action      The command type the runner dispatches. MUST exist in the runner's
            dispatch table; tests/test_suggestions.py fails otherwise.
template    OPTIONAL. A COMPLETE, PARSEABLE statement with slots. This is what
            makes a suggestion executable rather than merely descriptive, and
            what snippet_sync turns into a VS Code snippet. Slots:

                {locator}   a saved locator name  → becomes a dropdown of every
                            locator in both databases
                {text}      free text the operator types
                {number}    a number
                {variable}  a runtime variable name

            A template with its slots filled MUST parse via nlp.parser.parse_step
            and MUST yield `action`. Both are asserted by the tests, so a
            template cannot silently drift from the parser.
deprecated  OPTIONAL. True means the phrasing is kept for the record but the
            action is not dispatchable on any runner, so /nlp/suggest hides it
            and no snippet is generated. Remove the flag if it is implemented.
"""

KEYWORD_MAP = {
    # --- 1. NAVIGATION ---
    "justdial": {
        "phrases": [
            "open justdial", "navigate justdial", "go to justdial", 
            "visit justdial", "open jd", "launch justdial"
        ],
        "action": "open",
        # {url}, not {text}: the slot's NAME is what tells the step editor which
        # picker to offer — see SLOT_ROLE in nlp/fields.py. Called {text} it
        # offered free text and saved test data for a field that takes an
        # address, which is the wrong help at the one moment help is read.
        "template": "open {url}"
    },
    
    "close_browser": {
        "phrases": [
            "close browser", "close the browser", "exit browser", 
            "end session", "shutdown browser"
        ],
        "action": "close_browser",
        # No close command on either runner — the runner closes the browser
        # itself at end of run. Kept for the record only.
        "deprecated": True
    },

    # --- 2. WAITS & DELAYS ---
    # Logic: Unified all delay-based intents to one primary wait action.
    "wait_for_seconds": {
        "phrases": [
            "wait for", "sleep for", "pause for", "force wait", 
            "static wait", "wait", "delay", "hold", "pause"
        ],
        "action": "wait",
        "template": "wait {number} seconds"
    },

    "wait_for_resultpage_load": {
        "phrases": [
            "wait for result page load", "wait until result page loads", 
            "wait for results", "results load"
        ],
        "action": "wait_for_result_page_load",
        "template": "wait for result page load"
    },

    # --- 3. SEARCH & DATA CAPTURE ---
    "search": {
        "phrases": [
            "search for", "find", "look for", "enter search term", 
            "perform search for", "query"
        ],
        "action": "search",
        "template": "search for {text}"
    },

    "capture_business_name": {
        "phrases": [
            "capture business name", "get business name", "read name", 
            "extract business name", "store business name"
        ],
        "action": "capture_business_name",
        # Legacy engine.py action; not in either dispatch table.
        "deprecated": True
    },

    # --- 4. SCROLLING (ADVANCED) ---
    # Logic: Added dedicated triggers for the new scrolling capabilities.
    "scroll_page": {
        "phrases": ["scroll page", "scroll down", "scroll up", "vertical scroll"],
        "action": "scroll",
        "template": "scroll down {number}"
    },

    "scroll_until_element_visible": {
        "phrases": [
            "scroll until element visible", "scroll to element visible", 
            "scroll until element is visible", "find element by scrolling"
        ],
        "action": "scroll_until_element_visible",
        # Only the TEXT-based variant exists on the web runner — see
        # scroll_until_text_visible below.
        "deprecated": True
    },

    "scroll_until_text_visible": {
    "phrases": [
        "scroll until text",
        "scroll to text",
        "find text by scrolling"
    ],
    "action": "scroll_until_text_visible",
        "template": "scroll until text \"{text}\" visible"
    },

    # --- 5. ELEMENT INTERACTIONS ---
    "click_maybe_later": {
        "phrases": [
            "click maybe later", "dismiss popup", "skip login", 
            "maybe later", "close login popup"
        ],
        "action": "click_login_maybe_later",
        # JustDial-specific convenience that was never implemented.
        "deprecated": True
    },

    "click_first_result": {
        "phrases": [
            "first result", "top result", "select first result", 
            "click on first result"
        ],
        "action": "click_first_result",
        # Not in either dispatch table.
        "deprecated": True
    },

    # --- 6. VERIFICATIONS & VISUALS ---
    "verify_logic": {
        "phrases": [
            "verify", "check", "should see", "ensure", 
            "validate", "confirm", "assert"
        ],
        "action": "verify_text",
        "template": "verify text \"{text}\" on page" 
    },

    "verify_image_on_page": {
        "phrases": [
            "verify image on page", "image appears on page", 
            "match image on page", "check visual image"
        ],
        "action": "verify_image",
        "template": "verify image \"{text}\" on page"
    },

    # --- 7. VISIBILITY, CONDITION-BASED WAITS & OTP ---
    "verify_element_visible": {
        "phrases": [
            "verify element is visible", "check element is visible",
            "element should be visible", "assert element is visible",
            "verify element visible"
        ],
        "action": "verify_element_visible",
        "template": "verify element {locator} is visible"
    },

    "verify_element_not_visible": {
        "phrases": [
            "verify element is not visible", "check element is not visible",
            "element should not be visible", "assert element is not visible",
            "verify element not visible", "verify element does not appear",
            "element should not appear"
        ],
        "action": "verify_element_not_visible",
        "template": "verify element {locator} is not visible"
    },

    "verify_element_not_present": {
        "phrases": [
            "verify element is not present", "check element is not present",
            "element should not be present", "assert element is not present",
            "verify element not exists", "verify element is absent",
            "element should not exist in dom", "verify element removed"
        ],
        "action": "verify_element_not_exists",
        "template": "verify element {locator} is not present"
    },

    "wait_until_element_visible": {
        "phrases": [
            "wait until element is visible", "wait for element to appear",
            "wait until visible", "wait for element visible"
        ],
        "action": "wait_until_visible",
        "template": "wait until element {locator} is visible"
    },

    "wait_until_element_text_not": {
        "phrases": [
            "wait until element text is not", "wait for text to change",
            "wait until text changes", "wait until label changes"
        ],
        "action": "wait_until_text_not",
        "template": "wait until element {locator} text is not \"{text}\""
    },

    "enter_otp": {
        "phrases": [
            "enter otp", "type otp", "fill otp", "enter one time password",
            "enter otp into", "submit otp code"
        ],
        "action": "enter_otp",
        "template": "enter otp \"{text}\" into {locator}"
    },

    "fetch_otp_from_portal": {
        "phrases": [
            "fetch otp", "fetch otp for", "get otp from portal", "read otp from portal",
            "fetch otp for number", "retrieve otp", "pull otp from portal"
        ],
        "action": "fetch_otp",
        "template": "fetch otp for \"{text}\" as {variable}"
    },

    "verify_stored_variable_is_not": {
        "phrases": [
            "verify stored variable is not", "verify variable is not",
            "verify stored is not", "check variable changed",
            "assert variable is not"
        ],
        "action": "verify_var_not_equals",
        "template": "verify stored {variable} is not \"{text}\""
    },

    "screenshot": {
        "phrases": [
            "take screenshot", "capture screenshot", "screenshot", 
            "save screen"
        ],
        "action": "screenshot",
        "template": "take screenshot as {text}"
    },

    # --- 8. ELEMENT ACTIONS & TEXT ASSERTIONS ---
    # Added because generated flows use these but neither surface suggested
    # them — you had to already know the syntax to type them.
    "click_element": {
        "phrases": ["click", "click element", "click on element", "tap element", "press element"],
        "action": "click",
        "template": "click {locator}"
    },

    "type_into_element": {
        "phrases": ["type into", "type text into", "enter text into", "fill field", "input text"],
        "action": "fill",
        "template": "type \"{text}\" into {locator}"
    },

    "verify_element_exact_text": {
        "phrases": ["verify element has text", "element has exact text",
                    "assert element text equals", "check element text is"],
        "action": "verify_element_exact",
        "template": "verify element {locator} has text \"{text}\""
    },

    "verify_element_contains_text": {
        "phrases": ["verify element contains", "element contains text",
                    "assert element contains", "check element contains"],
        "action": "verify_element_contains",
        "template": "verify element {locator} contains \"{text}\""
    },

    # --- 9. STORING VALUES INTO RUNTIME VARIABLES ---
    "store_element_text": {
        "phrases": ["store text of", "save element text", "capture element text",
                    "read text of element", "store text"],
        "action": "extract_text",
        "template": "store text of {locator} as {variable}"
    },

    "store_element_attribute": {
        "phrases": ["store attribute", "save attribute of element", "capture attribute",
                    "read attribute of element"],
        "action": "extract_attribute",
        "template": "store attribute {text} of {locator} as {variable}"
    },

    "store_input_value": {
        "phrases": ["store value of", "save input value", "capture field value",
                    "read value of input"],
        "action": "extract_input",
        "template": "store value of {locator} as {variable}"
    },

    "store_element_count": {
        "phrases": ["store count of", "count elements", "save element count",
                    "how many elements"],
        "action": "extract_count",
        "template": "store count of {locator} as {variable}"
    },

    "verify_stored_variable_contains": {
        "phrases": ["verify stored contains", "stored variable contains",
                    "assert variable contains", "check stored value contains"],
        "action": "verify_var_contains",
        "template": "verify stored {variable} contains \"{text}\""
    },

    # --- 10. CONDITIONAL ACTIONS -------------------------------------------
    # Built and dispatchable, but absent from every suggestion surface — you had
    # to already know the syntax to use a feature you had already paid for.
    # Every template below was checked against the live parser.
    "tap_if_visible": {
        "phrases": ["click if visible", "tap if visible", "click only if visible",
                    "click element if visible", "optional click"],
        "action": "tap_if_visible",
        "template": "click if visible {locator}"
    },

    "verify_if_visible": {
        "phrases": ["verify if visible", "check if visible", "verify only if present"],
        "action": "verify_if_visible",
        "template": "verify if visible {locator}"
    },

    # --- 11. TABS & NAVIGATION ---------------------------------------------
    "close_tab": {
        "phrases": ["close tab", "close current tab"],
        "action": "close_tab", "template": "close tab"
    },
    "close_all_tabs": {
        "phrases": ["close all tabs", "close every tab"],
        "action": "close_all_tabs", "template": "close all tabs"
    },
    "list_tabs": {
        "phrases": ["list tabs", "show open tabs"],
        "action": "list_tabs", "template": "list tabs"
    },
    "switch_tab": {
        "phrases": ["switch to tab", "change tab", "go to tab"],
        "action": "switch_tab", "template": "switch to tab {number}"
    },
    # Named tabs are offered separately from the numbered form: an index is
    # only knowable if you have counted what is open, and the count changes the
    # moment a click opens a popup — which is when you need to switch.
    "switch_tab_named": {
        "phrases": ["switch to parent tab", "parent tab", "back to the tab that "
                    "opened this", "switch to child tab", "child tab",
                    "switch to new tab", "switch to last tab",
                    "switch to current tab", "switch to first tab"],
        "action": "switch_tab", "template": "switch to parent tab"
    },
    "open_new_tab": {
        "phrases": ["open new tab", "new tab", "open a new window"],
        "action": "open_new_tab", "template": "open new tab"
    },
    "open_in_new_tab": {
        "phrases": ["open in new tab", "open url in new tab", "open in a new window",
                    "open link in new tab", "new tab with url"],
        "action": "open_in_new_tab", "template": "open {url} in a new tab"
    },
    "go_forward": {
        "phrases": ["go forward", "forward"], "action": "go_forward",
        "template": "go forward"
    },
    "press_back": {
        "phrases": ["press back", "go back", "browser back"],
        "action": "press_back", "template": "press back"
    },
    "refresh": {
        "phrases": ["refresh", "reload page", "refresh page"],
        "action": "refresh", "template": "refresh page"
    },

    # --- 12. IFRAMES --------------------------------------------------------
    "switch_iframe": {
        "phrases": ["switch to iframe", "enter iframe", "go into frame"],
        "action": "switch_iframe", "template": "switch to iframe {locator}"
    },
    "exit_iframe": {
        "phrases": ["exit iframe", "leave iframe", "back to main frame"],
        "action": "exit_iframe", "template": "exit iframe"
    },

    # --- 13. PAGE-LEVEL CAPTURE --------------------------------------------
    "extract_title": {
        "phrases": ["store page title", "capture page title", "save title"],
        "action": "extract_title", "template": "store page title as {variable}"
    },
    "scroll_to": {
        "phrases": ["scroll to element", "scroll to"],
        "action": "scroll_to", "template": "scroll to {locator}"
    },
    "verify_exact_text": {
        "phrases": ["verify exact text", "page has exactly"],
        "action": "verify_exact_text", "template": "verify exact text \"{text}\""
    },
    "verify_multiple_texts": {
        "phrases": ["verify texts", "verify multiple texts", "check several texts"],
        "action": "verify_multiple_texts", "template": "verify texts \"{text}\""
    },

    # --- 14. JAVASCRIPT FALLBACKS ------------------------------------------
    # For elements an ordinary click cannot reach (overlays, custom widgets).
    "js_click": {
        "phrases": ["js click", "javascript click", "force click"],
        "action": "js_click", "template": "js click {locator}"
    },

    # --- 15. DATA & MATH ----------------------------------------------------
    "generate_fake": {
        "phrases": ["generate fake", "fake data", "random name"],
        "action": "generate_fake", "template": "generate fake name as {variable}"
    },
    "math": {
        "phrases": ["calculate", "compute", "math"],
        "action": "math", "template": "calculate {number} + {number} as {variable}"
    },
    "api_get": {
        "phrases": ["api get", "call api", "http get"],
        "action": "api_get", "template": "api get \"{text}\" as {variable}"
    },

    # --- 16. BROWSER ALERTS / COOKIES / UPLOAD  (Testsigma parity) ----------
    "accept_alert": {
        "phrases": ["accept alert", "ok alert", "confirm alert"],
        "action": "accept_alert", "template": "accept alert"},
    "dismiss_alert": {
        "phrases": ["dismiss alert", "cancel alert", "close alert"],
        "action": "dismiss_alert", "template": "dismiss alert"},
    "verify_alert_present": {
        "phrases": ["verify alert is present", "alert should appear"],
        "action": "verify_alert_present", "template": "verify alert is present"},
    "verify_alert_text": {
        "phrases": ["verify alert text", "alert says"],
        "action": "verify_alert_text", "template": "verify alert text \"{text}\""},
    "type_into_alert": {
        "phrases": ["type into alert", "answer prompt"],
        "action": "type_into_alert", "template": "type \"{text}\" into alert"},
    "delete_all_cookies": {
        "phrases": ["delete all cookies", "clear cookies"],
        "action": "delete_all_cookies", "template": "delete all cookies"},
    "delete_cookie": {
        "phrases": ["delete cookie", "remove cookie"],
        "action": "delete_cookie", "template": "delete cookie \"{text}\""},
    "verify_cookie": {
        "phrases": ["verify cookie", "cookie should exist"],
        "action": "verify_cookie", "template": "verify cookie \"{text}\" exists"},
    "upload_file": {
        "phrases": ["upload file", "attach file", "choose file"],
        "action": "upload_file", "template": "upload file \"{text}\" to {locator}"},
    "switch_window_title": {
        "phrases": ["switch to window title", "go to window named"],
        "action": "switch_window_title",
        "template": "switch to window title \"{text}\""},
    "parent_frame": {
        "phrases": ["switch to parent frame", "go to parent frame"],
        "action": "parent_frame", "template": "switch to parent frame"},

    # --- 17. TEXT MATCHING — beyond contains and exact -----------------------
    # Neither this framework nor Testsigma had starts-with, ends-with or regex.
    # They are what you need when only part of the text is stable: an order id
    # with a fixed prefix, a price with a fixed currency, a message whose wording
    # changes but whose shape does not.
    "verify_page_contains": {
        "phrases": ["verify page contains", "page contains", "verify page has"],
        "action": "verify_page_contains",
        "template": "verify page contains \"{text}\""},
    "verify_page_not_contains": {
        "phrases": ["verify page does not contain", "page should not contain"],
        "action": "verify_page_not_contains",
        "template": "verify page does not contain \"{text}\""},
    "verify_page_starts": {
        "phrases": ["verify page starts with", "page begins with"],
        "action": "verify_page_starts",
        "template": "verify page starts with \"{text}\""},
    "verify_page_ends": {
        "phrases": ["verify page ends with"],
        "action": "verify_page_ends",
        "template": "verify page ends with \"{text}\""},
    "verify_page_matches": {
        "phrases": ["verify page matches", "verify page regex", "page matches pattern"],
        "action": "verify_page_matches",
        "template": "verify page matches \"{text}\""},
    "verify_title_contains": {
        "phrases": ["verify page title contains", "title contains"],
        "action": "verify_title_contains",
        "template": "verify page title contains \"{text}\""},
    "verify_title_exact": {
        "phrases": ["verify page title is", "title equals"],
        "action": "verify_title_exact",
        "template": "verify page title is \"{text}\""},
    "verify_element_starts": {
        "phrases": ["verify element starts with", "element begins with"],
        "action": "verify_element_starts",
        "template": "verify element {locator} starts with \"{text}\""},
    "verify_element_ends": {
        "phrases": ["verify element ends with"],
        "action": "verify_element_ends",
        "template": "verify element {locator} ends with \"{text}\""},
    "verify_element_matches": {
        "phrases": ["verify element matches", "verify element regex"],
        "action": "verify_element_matches",
        "template": "verify element {locator} matches \"{text}\""},
    "verify_element_not_contains": {
        "phrases": ["verify element does not contain", "element should not contain"],
        "action": "verify_element_not_contains",
        "template": "verify element {locator} does not contain \"{text}\""},

    "browser_permission": {
        "phrases": ["allow browser permission", "deny browser permission",
                    "grant permission", "block permission",
                    "allow geolocation", "deny notifications"],
        "action": "browser_permission",
        "template": "allow browser permission {text}"
    },

    "create_runtime_variable": {
        "phrases": ["create variable", "set variable", "define variable",
                    "store value as variable"],
        "action": "create_variable",
        "template": "create variable {variable} with value \"{text}\""
    }
}
