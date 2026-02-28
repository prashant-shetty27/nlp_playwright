"""
nlp/parser.py
Single unified NLP/DSL step parser (merged from root command_parser.py and dsl/parser.py).
Supports all NLP step syntax used in .flow files.
"""
import re
from nlp.command import Command


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
            wait=float(wait.group(1)) if wait else 1,
        )

    # =============================
    # VERIFY IMAGE (from dsl/parser.py)
    # =============================
    if s.lower().startswith("verify image"):
        match = re.search(r'"(.*?)"', s)
        if not match:
            raise ValueError(f"Invalid verify image syntax: {s}")
        image_path = match.group(1)

        thresh_match = re.search(r"threshold\s+(\d+)%", s, re.IGNORECASE)
        threshold = float(thresh_match.group(1)) / 100.0 if thresh_match else 0.5

        return Command(type="verify_image", image_path=image_path, threshold=threshold)

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
            count=int(scroll.group(1)) if scroll else None,
        )

    # =============================
    # REFRESH PAGE
    # =============================
    if s.lower() in ["refresh", "refresh page", "reload", "reload page"]:
        return Command(type="refresh")

    # =============================
    # CLICK COMMAND
    # =============================
    if s.lower().startswith("click "):
        target = re.sub(r"^click\s+(on\s+)?(element\s+)?", "", s, flags=re.IGNORECASE).strip()
        return Command(type="click", target=target)

    # =============================
    # FILL / TYPE COMMAND
    # =============================
    # Matches: type "hello" into search_box  OR  fill "hello" in search_box
    if s.lower().startswith("type ") or s.lower().startswith("fill "):
        pattern = r"^(?:type|fill)\s+\"(.*?)\"\s+(?:into|in)\s+(.*)"
        match = re.search(pattern, s, re.IGNORECASE)
        if match:
            text_to_type, target = match.groups()
            return Command(type="fill", text=text_to_type, target=target.strip())

    # =============================
    # TERMINAL FALLBACK
    # =============================
    raise ValueError(f"Unknown command: {step}")
