import re
from command_model import Command

def parse_step(step: str) -> Command:
    s = step.strip()

    # =============================
    # OPEN
    # =============================
    if s.lower().startswith("open "):
        return Command(type="open", target=s[5:].strip())

    # =============================
    # SEARCH
    # =============================
    if s.lower().startswith("search for "):
        return Command(type="search", text=s[11:].strip())

    # =============================
    # WAIT (Specific)
    # =============================
    if s.lower() in ["wait for result page load", "results load", "wait for results"]:
        return Command(type="wait_for_result_page_load")

    # =============================
    # WAIT (Seconds)
    # =============================
    m = re.fullmatch(r"wait\s+(\d+(\.\d+)?)\s*seconds?", s, re.I)
    if m:
        return Command(type="wait", wait=float(m.group(1)))

    # =============================
    # SCROLL UNTIL TEXT VISIBLE
    # =============================
    if s.lower().startswith("scroll until text"):
        text = re.search(r'"(.*?)"', s)
        if not text:
            raise ValueError("Scroll requires quoted text")

        count = re.search(r"scroll count\s*(\d+)", s, re.I)
        wait = re.search(r"scroll wait\s*(\d+(\.\d+)?)", s, re.I)

        return Command(
            type="scroll_until_text_visible",
            text=text.group(1),
            count=int(count.group(1)) if count else 10,
            wait=float(wait.group(1)) if wait else 1
        )

    # =============================
    # VERIFY TEXT
    # =============================
    if s.lower().startswith("verify text"):
        text = re.search(r'"(.*?)"', s)
        if not text:
            raise ValueError("Verify requires quoted text")

        scroll = re.search(r",\s*(\d+)\s*$", s)

        return Command(
            type="verify_text",
            text=text.group(1),
            count=int(scroll.group(1)) if scroll else None
        )
        
    # =============================
    # REFRESH PAGE
    # =============================
    if s.lower() in ["refresh", "refresh page", "reload", "reload page"]:
        return Command(type="refresh")
        
    # =============================
    # CLICK COMMAND
    # =============================
    # This single regex handles: "click x", "click on x", and "click on element x"
    if s.lower().startswith("click "):
        target = re.sub(r"^click\s+(on\s+)?(element\s+)?", "", s, flags=re.IGNORECASE).strip()
        return Command(type="click", target=target)
    # =============================
    # TERMINAL FALLBACK
    # =============================
    # This MUST be the absolute last line of the function.
    raise ValueError(f"Unknown command: {step}")
    # =============================
    # FILL / TYPE COMMAND
    # =============================
    # Matches: type "hello" into search_box OR fill "hello" in search_box
    if s.lower().startswith("type ") or s.lower().startswith("fill "):
        pattern = r"^(?:type|fill)\s+\"(.*?)\"\s+(?:into|in)\s+(.*)"
        match = re.search(pattern, s, re.IGNORECASE)
        if match:
            text_to_type, target = match.groups()
            # We standardize both 'type' and 'fill' into a single 'fill' command
            return Command(type="fill", text=text_to_type, target=target.strip())