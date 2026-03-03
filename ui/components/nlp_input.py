"""
ui/components/nlp_input.py — NLP Step Autocomplete Input

A smart text input that suggests NLP keywords and element names as the user types.
Core UX component for writing flow steps without memorizing syntax.

Autocomplete sources (merged, ordered by relevance):
    1. NLP action keywords from nlp/keywords.py
       e.g. "go to", "click", "type", "verify text", "wait", "tap", "swipe"
    2. Element names from the platform's element repository
       e.g. "login_button", "search_field", "saerchBarHomePage"
    3. Common patterns (action + element combos from existing flows)
       e.g. "click login_button", "type '...' in search_field"
    4. ${variable_name} tokens from data/common/variables.json

Behavior:
    - Dropdown appears after 1 character typed
    - Keyword suggestions shown first, then element names
    - Selecting a suggestion fills the input and positions cursor
    - Tab key → accepts first suggestion
    - Esc → dismiss dropdown

Props:
    platform:       str — "web" | "android" | "ios" (determines element source)
    on_submit:      callable(nlp_text: str) — called on Enter or suggestion select
    placeholder:    str (default "Type a step, e.g. 'click login button'")
    initial_value:  str (default "")

Used in: pages/platform/*/test_cases.py, pages/platform/*/recorder.py
"""
