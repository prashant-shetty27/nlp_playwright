"""
core/healer.py
ML-powered self-healing orchestrator — unified for all platforms.
Works with Playwright (web/mobile) and Appium (android/ios/hybrid).
"""
import logging
from healing.ml_engine import LocatorHealer

logger = logging.getLogger(__name__)

_ml_healer = LocatorHealer()


def _xpath_literal(value: str) -> str:
    """
    Build a safe XPath string literal for values that may contain quotes.
    """
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    segments: list[str] = []
    for i, part in enumerate(parts):
        if part:
            segments.append(f"'{part}'")
        if i < len(parts) - 1:
            segments.append('"\'"')
    return f"concat({', '.join(segments)})"


def scrape_dom_web(page) -> list:
    """
    Scrapes visible DOM elements from a Playwright page.
    Used for web and mobile-web healing.
    """
    logger.info("🔍 Scraping DOM for ML candidates (web)...")
    js_payload = """
    () => {
        const elements = Array.from(document.querySelectorAll('*'));
        const candidates = elements.map(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return null;
            let attributesData = {};
            for (let i = 0; i < el.attributes.length; i++) {
                let attr = el.attributes[i];
                if (attr.value && attr.value.length < 300) {
                    attributesData[attr.name] = attr.value;
                }
            }
            return {
                tagName:    el.tagName.toLowerCase(),
                className:  el.className || null,
                innerText:  el.innerText ? el.innerText.substring(0, 100).trim() : null,
                rect: {
                    x:      Math.round(rect.x),
                    y:      Math.round(rect.y),
                    width:  Math.round(rect.width),
                    height: Math.round(rect.height)
                },
                attributes: attributesData
            };
        });
        return candidates.filter(e => e !== null);
    }
    """
    return page.evaluate(js_payload)


def build_locator_from_dna(element_dna: dict) -> str:
    """
    Converts winning ML element DNA into a usable XPath locator string.
    Works for web, mobile-web, and hybrid apps.
    """
    tag = element_dna.get("tagName", "*")
    attrs = element_dna.get("attributes", {}) or {}

    if attrs.get("id"):
        return f"//{tag}[@id={_xpath_literal(attrs['id'])}]"
    if attrs.get("name"):
        return f"//{tag}[@name={_xpath_literal(attrs['name'])}]"
    if attrs.get("aria-label"):
        return f"//{tag}[@aria-label={_xpath_literal(attrs['aria-label'])}]"
    if attrs.get("title"):
        return f"//{tag}[@title={_xpath_literal(attrs['title'])}]"
    if attrs.get("alt"):
        return f"//{tag}[@alt={_xpath_literal(attrs['alt'])}]"

    classes = attrs.get("class", "")
    if classes:
        valid_classes = [c for c in classes.split() if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join(
                [f"contains(@class, {_xpath_literal(c)})" for c in valid_classes]
            )
            return f"//{tag}[{contains_logic}]"

    text = element_dna.get("innerText")
    if text and len(text) < 40:
        return f"//{tag}[normalize-space(text())={_xpath_literal(text)}]"

    return f"//{tag}"


def ml_heal_element(page, target_dna: dict, locator_name: str = "") -> str | None:
    """
    Self-healing orchestration for web / mobile-web (Playwright).
    Scrapes current DOM, runs ML, returns healed XPath or None.

    When `locator_name` is given and the match is confident, the result is
    written back to the locator database as an alternate — so the next run
    resolves it without another DOM scrape and ML pass, and a selector that has
    genuinely moved stops being silently re-derived forever.
    """
    candidates = scrape_dom_web(page)
    logger.info("🧠 ML Engine analyzing %d candidates...", len(candidates))

    winner_dna = _ml_healer.train_and_predict(target_dna, candidates)
    if not winner_dna:
        logger.error("❌ ML Engine could not confidently match an element.")
        return None

    # A different tag is a different kind of control (a <select> healed to
    # an <input>, an <a> to a <span>): accepted only when a strong identity
    # attribute matched exactly.
    t_tag = str(target_dna.get("tagName") or "").lower()
    w_tag = str(winner_dna.get("tagName") or "").lower()
    if t_tag and w_tag and t_tag != w_tag:
        t_attrs = target_dna.get("attributes") or {}
        w_attrs = winner_dna.get("attributes") or {}
        strong = any(t_attrs.get(k) and t_attrs.get(k) == w_attrs.get(k)
                     for k in ("id", "data-testid", "name", "aria-label"))
        if not strong:
            logger.warning("⚠️  Heal rejected: winner is <%s>, the saved element is <%s> "
                           "and no id/name/testid matched.", w_tag, t_tag)
            return None

    healed = build_locator_from_dna(winner_dna)
    if not healed:
        return None
    healed = _pin_to_winner(page, healed, winner_dna)
    if not healed:
        return None
    if locator_name:
        # Not written to the locator database yet: the caller confirms the
        # heal once the click / fill / check actually succeeded with it
        # (confirm_heal). Recording first meant two failing attempts could
        # promote a selector that never worked.
        _PENDING_HEALS[locator_name] = (healed, winner_dna)
    return healed


#: Heals produced this run that have not yet been proven by a successful action.
_PENDING_HEALS: dict[str, tuple[str, dict]] = {}


def confirm_heal(locator_name: str) -> None:
    """The healed selector just worked — now it may be remembered."""
    pending = _PENDING_HEALS.pop(locator_name, None)
    if not pending:
        return
    healed, winner_dna = pending
    try:
        from locators.healing_memory import record_heal
        report = record_heal(locator_name, healed,
                             score=float(winner_dna.get("_heal_score", 0.0)),
                             original_failed=True, dna=winner_dna)
    except Exception as e:  # noqa: BLE001 — remembering is a bonus, never a failure
        logger.warning("Could not record heal for %r: %s", locator_name, e)
        return
    if report.get("promoted"):
        logger.info("🏥 %r now resolves to the healed selector by default.", locator_name)
    elif report.get("stored"):
        from locators.healing_memory import PROMOTE_AFTER
        logger.info("🏥 Remembered healed selector for %r (use %d/%d before it "
                    "becomes primary).", locator_name, report["uses"], PROMOTE_AFTER)


def _pin_to_winner(page, healed: str, winner_dna: dict) -> str | None:
    """
    Make sure `healed` points at the element the ML picked, not merely at
    the first node with the same tag or class.

    build_locator_from_dna can fall back to `//div[contains(@class,'a')]`
    or even `//button`; `.first` on that clicked a different element and the
    step still reported "healed". When the XPath matches several nodes, the
    one whose box matches the winner's is picked by index; when it matches
    none, the heal is rejected.
    """
    rect = winner_dna.get("rect") or {}
    try:
        boxes = page.evaluate(
            """(xp) => { const out = []; const r = document.evaluate(xp, document, null,
                 XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                 for (let i = 0; i < r.snapshotLength; i++) {
                   const b = r.snapshotItem(i).getBoundingClientRect();
                   out.push([Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]);
                 } return out; }""", healed)
    except Exception as e:  # noqa: BLE001 — a bad XPath is a rejected heal
        logger.warning("⚠️  Heal rejected: healed XPath could not be evaluated (%s)", e)
        return None
    if not boxes:
        logger.warning("⚠️  Heal rejected: %s matches nothing on the page", healed)
        return None
    if len(boxes) == 1:
        return healed
    want = (rect.get("x"), rect.get("y"), rect.get("width"), rect.get("height"))
    for i, b in enumerate(boxes, 1):
        if all(v is not None for v in want) and all(abs(int(b[k]) - int(want[k])) <= 2 for k in range(4)):
            logger.info("🏥 Healed XPath matched %d nodes; pinned to #%d by position.", len(boxes), i)
            return f"({healed})[{i}]"
    logger.warning("⚠️  Heal rejected: %s matches %d nodes and none sits where the "
                   "ML winner was.", healed, len(boxes))
    return None


def ml_heal_element_appium(driver, target_dna: dict) -> str | None:
    """
    Self-healing orchestration for native apps (Appium — Android / iOS).
    Scrapes page source XML, runs ML, returns healed locator or None.
    Stub — extend with Appium-specific DOM scraping as needed.
    """
    logger.info("🔍 Appium healing stub — extend for Android/iOS DOM scraping.")
    # TODO: implement Appium page source XML scraping and featurization
    return None
