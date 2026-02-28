"""
healing/locator_builder.py
Converts ML-winning element DNA into a Playwright locator string.
Framework-agnostic naming: "locator" covers XPath, CSS, role, text selectors.
Extracted from healer.py — single responsibility.

Priority order:
  1. Developer IDs (data-testid, id, name)        — most stable, explicit intent
  2. Accessibility / semantic (aria-label, title, alt, role+type)
  3. Class logic (strips brittle font/utility classes)
  4. Text content
  5. Tag-only fallback
"""


def generate_locator(element_dna: dict) -> str:
    """
    Converts a winning ML element payload into a Playwright-compatible locator string.
    Currently returns XPath; designed to be extended with CSS/role selectors.
    """
    tag = element_dna.get("tagName", "*")
    attrs = element_dna.get("attributes", {})

    # 1. Developer IDs — most precise
    if attrs.get("data-testid"):
        return f"//{tag}[@data-testid='{attrs['data-testid']}']"
    if attrs.get("id"):
        return f"//{tag}[@id='{attrs['id']}']"
    if attrs.get("name"):
        return f"//{tag}[@name='{attrs['name']}']"

    # 2. Accessibility & semantic attributes
    if attrs.get("aria-label"):
        return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"):
        return f"//{tag}[@title='{attrs['title']}']"
    if attrs.get("alt"):
        return f"//{tag}[@alt='{attrs['alt']}']"
    if attrs.get("role") and attrs.get("type"):
        return f"//{tag}[@role='{attrs['role']}' and @type='{attrs['type']}']"

    # 3. Class logic — individual tokens, skipping brittle utility classes
    _BLOCKLIST = ("font", "animate", "transition", "hover", "active", "focus", "visited")
    class_str = attrs.get("class", "")
    if class_str:
        stable = [c for c in class_str.split() if not any(b in c.lower() for b in _BLOCKLIST)]
        if stable:
            contains_logic = " and ".join(f"contains(@class, '{c}')" for c in stable[:3])
            return f"//{tag}[{contains_logic}]"

    # 4. Text content — only for short, stable labels
    text = element_dna.get("innerText") or ""
    text = text.strip()
    if text and len(text) < 40:
        clean = text.replace("'", "\\'")
        return f"//{tag}[normalize-space(text())='{clean}']"

    # 5. Tag-only fallback
    return f"//{tag}"
