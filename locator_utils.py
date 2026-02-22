def get_all_locators():
    """Load and merge locators from both locators_manual.json and recorded_elements.json."""
    import os, json
    locator_mapping = {}
    # Load manual locators
    if os.path.exists('locators_manual.json'):
        try:
            with open('locators_manual.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                for page_name, locators in data.items():
                    if isinstance(locators, dict):
                        for element_name in locators.keys():
                            display_text = f"{page_name.upper()} ➔ {element_name}"
                            locator_mapping[element_name] = display_text
        except Exception:
            pass
    # Load recorded locators
    if os.path.exists('recorded_elements.json'):
        try:
            with open('recorded_elements.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                for page_name, locators in data.items():
                    if isinstance(locators, dict):
                        for element_name in locators.keys():
                            display_text = f"{page_name.upper()} ➔ {element_name}"
                            locator_mapping[element_name] = display_text
        except Exception:
            pass
    return locator_mapping
