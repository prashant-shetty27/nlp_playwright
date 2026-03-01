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
    # VERIFY EXACT TEXT (full element text must match completely)
    # verify exact text "Sort by"
    # =============================
    if re.match(r'^verify\s+exact\s+text\s+', s, re.I):
        text = re.search(r'"(.*?)"', s)
        if not text:
            raise ValueError("Verify exact text requires quoted text")
        return Command(type="verify_exact_text", text=text.group(1))

    # =============================
    # VERIFY TEXT  (singular — partial/contains match, must NOT match "verify texts ...")
    # =============================
    if re.match(r'^verify\s+text\s+', s, re.I):
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
    # SCREENSHOT
    # screenshot | take screenshot | capture screenshot | take screenshot as <label>
    # =============================
    if re.match(r'^(?:take\s+|capture\s+)?screenshot', s, re.I):
        label_match = re.search(r'\bas\s+(\w+)\b', s, re.I)
        return Command(type="screenshot", target=label_match.group(1) if label_match else "capture")

    # =============================
    # VERTICAL SCROLL
    # scroll down | scroll up | scroll down N times | scroll down N
    # =============================
    m = re.match(r'^scroll\s+(down|up)(?:\s+(\d+)\s+times?|\s+(\d+))?$', s, re.I)
    if m:
        direction = m.group(1).lower()
        times  = int(m.group(2)) if m.group(2) else None   # "3 times"
        pixels = int(m.group(3)) if m.group(3) else None   # raw pixel override
        amount = (times * 500) if times else (pixels if pixels else 500)
        if direction == "up":
            amount = -amount
        return Command(type="scroll", count=amount)

    # =============================
    # VERIFY ELEMENT EXACT TEXT
    # verify element <locator> has text "<text>"
    # =============================
    m = re.match(r'^verify\s+element\s+(\S+)\s+has\s+text\s+"(.*?)"$', s, re.I)
    if m:
        locator, text = m.groups()
        return Command(type="verify_element_exact", target=locator, text=text)

    # =============================
    # VERIFY ELEMENT CONTAINS TEXT
    # verify element <locator> contains "<text>"
    # =============================
    m = re.match(r'^verify\s+element\s+(\S+)\s+contains\s+"(.*?)"$', s, re.I)
    if m:
        locator, text = m.groups()
        return Command(type="verify_element_contains", target=locator, text=text)

    # =============================
    # VERIFY MULTIPLE GLOBAL TEXTS
    # verify texts "a", "b", "c"
    # =============================
    if re.match(r'^verify\s+texts?\s+', s, re.I):
        payload = re.sub(r'^verify\s+texts?\s+', '', s, flags=re.I).strip()
        # Build a comma-separated bare string: "a", "b" → a,b
        parts = re.findall(r'"(.*?)"', payload)
        texts = ",".join(parts) if parts else payload
        return Command(type="verify_multiple_texts", text=texts)

    # =============================
    # STORE TEXT OF <locator> AS <var>   (extract element inner text)
    # =============================
    m = re.match(r'^store\s+text\s+of\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        locator, var_name = m.groups()
        return Command(type="extract_text", target=locator, variable_name=var_name)

    # =============================
    # STORE PAGE URL / TITLE
    # store page url as <var>  |  store page title as <var>
    # =============================
    m = re.match(r'^store\s+page\s+url\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="extract_url", variable_name=m.group(1))

    m = re.match(r'^store\s+page\s+title\s+as\s+(\S+)$', s, re.I)
    if m:
        return Command(type="extract_title", variable_name=m.group(1))

    # =============================
    # STORE ATTRIBUTE <attr> OF <locator> AS <var>
    # =============================
    m = re.match(r'^store\s+attribute\s+(\S+)\s+of\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        attr, locator, var_name = m.groups()
        return Command(type="extract_attribute", target=locator, attribute=attr, variable_name=var_name)

    # =============================
    # STORE VALUE OF <locator> AS <var>   (input element value)
    # =============================
    m = re.match(r'^store\s+value\s+of\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        locator, var_name = m.groups()
        return Command(type="extract_input", target=locator, variable_name=var_name)

    # =============================
    # STORE COUNT OF <locator> AS <var>
    # =============================
    m = re.match(r'^store\s+count\s+of\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        locator, var_name = m.groups()
        return Command(type="extract_count", target=locator, variable_name=var_name)

    # =============================
    # CREATE / STORE LITERAL VARIABLE
    # store "value" as <var>  |  create variable <var> with value "value"
    # Must come AFTER all other "store X of Y" patterns above.
    # =============================
    m = re.match(r'^store\s+"(.*?)"\s+as\s+(\S+)$', s, re.I)
    if m:
        value, var_name = m.groups()
        return Command(type="create_variable", target=var_name, text=value)

    m = re.match(r'^create\s+variable\s+(\S+)\s+(?:with\s+value\s+)?"(.*?)"$', s, re.I)
    if m:
        var_name, value = m.groups()
        return Command(type="create_variable", target=var_name, text=value)

    # =============================
    # MATH / CALCULATE
    # calculate <n1> +|-|*|/ <n2> as <var>
    # =============================
    m = re.match(r'^calculate\s+(\S+)\s+([+\-*/])\s+(\S+)\s+as\s+(\S+)$', s, re.I)
    if m:
        num1, op, num2, var_name = m.groups()
        return Command(type="math", target=num1, text=op, values=[num2], variable_name=var_name)

    # =============================
    # VERIFY STORED VARIABLE CONTAINS
    # verify <var> contains "<partial>"  |  verify stored <var> contains "<partial>"
    # =============================
    m = re.match(r'^verify\s+(?:stored\s+)?(?:variable\s+)?(\S+)\s+contains\s+"(.*?)"$', s, re.I)
    if m:
        var_name, partial = m.groups()
        return Command(type="verify_var_contains", target=var_name, text=partial)

    # =============================
    # TERMINAL FALLBACK
    # =============================
    raise ValueError(f"Unknown command: {step}")
