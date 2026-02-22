from dsl.command import Command
import re

def parse_step(step: str) -> Command:
    """
    Converts DSL step text into Command object.

    Supported syntax:
        open <site>
        wait <seconds>
        search for <text>
        scroll until text "<text>" visible, scroll count <n>, scroll wait <n>
        verify image "<file>" on page with threshold <percent>
    """

    tokens = step.strip().split()
    if not tokens:
        raise ValueError("Empty step")

    cmd = tokens[0].lower()

    # -----------------------------
    # OPEN COMMAND
    # -----------------------------
    if cmd == "open":
        if len(tokens) != 2:
            raise ValueError(f"Invalid open syntax: {step}")
        return Command(type="open", target=tokens[1])

    # -----------------------------
    # WAIT COMMAND
    # -----------------------------
    elif cmd == "wait":
        # allow "wait 2 second(s)" as well
        try:
            seconds = float(tokens[1])
        except ValueError:
            raise ValueError(f"Invalid wait value: {tokens[1]}")
        return Command(type="wait", wait=seconds)

    # -----------------------------
    # SEARCH COMMAND
    # -----------------------------
    elif cmd == "search":
        # Example: search for Restaurants
        text = step[len("search for"):].strip()
        return Command(type="search", text=text)

    # -----------------------------
    # SCROLL UNTIL TEXT VISIBLE
    # -----------------------------
    elif cmd == "scroll":
        # Example: scroll until text "Explore More" visible, scroll count 5, scroll wait 1
        text_match = re.search(r'"(.*?)"', step)
        text = text_match.group(1) if text_match else None

        count_match = re.search(r"scroll count\s+(\d+)", step, re.IGNORECASE)
        count = int(count_match.group(1)) if count_match else None

        wait_match = re.search(r"scroll wait\s+(\d+)", step, re.IGNORECASE)
        wait = int(wait_match.group(1)) if wait_match else None

        return Command(type="scroll_until_text_visible", text=text, count=count, wait=wait)

    # -----------------------------
    # VERIFY IMAGE COMMAND
    # -----------------------------
    elif cmd == "verify":
        if len(tokens) >= 2 and tokens[1].lower() == "image":
            match = re.search(r'"(.*?)"', step)
            if not match:
                raise ValueError(f"Invalid verify image syntax: {step}")
            image_path = match.group(1)

            thresh_match = re.search(r"threshold\s+(\d+)%", step, re.IGNORECASE)
            threshold = float(thresh_match.group(1)) / 100.0 if thresh_match else 0.5

            return Command(type="verify_image", image_path=image_path, threshold=threshold)

    # -----------------------------
    # UNKNOWN COMMAND
    # -----------------------------
    raise ValueError(f"Unknown command: {cmd}")
