"""
File: nlp_keywords.py
Description: The NLP Keyword Map translates natural language phrases into 
specific internal action triggers for the execution engine.
"""

KEYWORD_MAP = {
    # --- 1. NAVIGATION ---
    "justdial": {
        "phrases": [
            "open justdial", "navigate justdial", "go to justdial", 
            "visit justdial", "open jd", "launch justdial"
        ],
        "action": "open_justdial"
    },
    
    "close_browser": {
        "phrases": [
            "close browser", "close the browser", "exit browser", 
            "end session", "shutdown browser"
        ],
        "action": "close_browser"
    },

    # --- 2. WAITS & DELAYS ---
    # Logic: Unified all delay-based intents to one primary wait action.
    "wait_for_seconds": {
        "phrases": [
            "wait for", "sleep for", "pause for", "force wait", 
            "static wait", "wait", "delay", "hold", "pause"
        ],
        "action": "wait_for" 
    },

    "wait_for_resultpage_load": {
        "phrases": [
            "wait for result page load", "wait until result page loads", 
            "wait for results", "results load"
        ],
        "action": "wait_for_result_page_load"
    },

    # --- 3. SEARCH & DATA CAPTURE ---
    "search": {
        "phrases": [
            "search for", "find", "look for", "enter search term", 
            "perform search for", "query"
        ],
        "action": "search"
    },

    "capture_business_name": {
        "phrases": [
            "capture business name", "get business name", "read name", 
            "extract business name", "store business name"
        ],
        "action": "capture_business_name"
    },

    # --- 4. SCROLLING (ADVANCED) ---
    # Logic: Added dedicated triggers for the new scrolling capabilities.
    "scroll_page": {
        "phrases": ["scroll page", "scroll down", "scroll up", "vertical scroll"],
        "action": "vertical_scroll"
    },

    "scroll_until_element_visible": {
        "phrases": [
            "scroll until element visible", "scroll to element visible", 
            "scroll until element is visible", "find element by scrolling"
        ],
        "action": "scroll_until_element_visible"
    },

    "scroll_until_text_visible": {
    "phrases": [
        "scroll until text",
        "scroll to text",
        "find text by scrolling"
    ],
    "action": "scroll_until_text_visible"
    },

    # --- 5. ELEMENT INTERACTIONS ---
    "click_maybe_later": {
        "phrases": [
            "click maybe later", "dismiss popup", "skip login", 
            "maybe later", "close login popup"
        ],
        "action": "click_login_maybe_later"
    },

    "click_first_result": {
        "phrases": [
            "first result", "top result", "select first result", 
            "click on first result"
        ],
        "action": "click_first_result"
    },

    # --- 6. VERIFICATIONS & VISUALS ---
    "verify_logic": {
        "phrases": [
            "verify", "check", "should see", "ensure", 
            "validate", "confirm", "assert"
        ],
        "action": "universal_verify" 
    },

    "verify_image_on_page": {
        "phrases": [
            "verify image on page", "image appears on page", 
            "match image on page", "check visual image"
        ],
        "action": "verify_image_on_page"
    },

    "screenshot": {
        "phrases": [
            "take screenshot", "capture screenshot", "screenshot", 
            "save screen"
        ],
        "action": "take_screenshot"
    }
}