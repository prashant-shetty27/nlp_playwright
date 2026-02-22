import logging
from playwright.sync_api import Page

logger = logging.getLogger(__name__)

def score_element(locator_text: str, hint: str) -> int:
    """
    Very simple 'AI-style' scoring function.
    Higher score = better match.
    """

    locator_text = locator_text.lower()
    hint = hint.lower()

    score = 0

    if hint in locator_text:
        score += 10

    if "button" in locator_text or "btn" in locator_text:
        score += 3

    if "aria-label" in locator_text:
        score += 2

    if "placeholder" in locator_text:
        score += 2

    if len(locator_text) < 80:   # prefer shorter, cleaner locators
        score += 2

    return score


def find_best_match(page: Page, hint: str, element_type: str):
    """
    AI-like search across the page for best matching element.
    """

    candidates = []

    # Scan all elements of that type
    elements = page.locator(f"//{element_type}").all()

    for i, el in enumerate(elements):
        try:
            text = el.inner_text()
            outer = el.evaluate("e => e.outerHTML")

            combined = (text + outer).lower()

            score = score_element(combined, hint)

            if score > 0:
                candidates.append({
                    "index": i,
                    "text": text.strip(),
                    "outer": outer[:200],
                    "score": score
                })

        except:
            pass

    # Pick highest score
    if not candidates:
        return None

    best = max(candidates, key=lambda x: x["score"])

    # Build a stable locator
    healed_locator = f"//{element_type}[{best['index'] + 1}]"

    logger.info("🤖 AI-Healed Locator: %s", healed_locator)
    logger.info("Score: %s", best["score"])
    logger.info("Matched text: %s", best["text"])

    return healed_locator
