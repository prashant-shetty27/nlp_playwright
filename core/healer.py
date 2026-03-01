"""
core/healer.py
ML-powered self-healing orchestrator — unified for all platforms.
Works with Playwright (web/mobile) and Appium (android/ios/hybrid).
"""
import logging
from core.ml_engine import LocatorHealer

logger = logging.getLogger(__name__)

_ml_healer = LocatorHealer()


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
        return f"//{tag}[@id='{attrs['id']}']"
    if attrs.get("name"):
        return f"//{tag}[@name='{attrs['name']}']"
    if attrs.get("aria-label"):
        return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"):
        return f"//{tag}[@title='{attrs['title']}']"
    if attrs.get("alt"):
        return f"//{tag}[@alt='{attrs['alt']}']"

    classes = attrs.get("class", "")
    if classes:
        valid_classes = [c for c in classes.split() if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join([f"contains(@class, '{c}')" for c in valid_classes])
            return f"//{tag}[{contains_logic}]"

    text = element_dna.get("innerText")
    if text and len(text) < 40:
        clean_text = text.replace("'", "\\'")
        return f"//{tag}[normalize-space(text())='{clean_text}']"

    return f"//{tag}"


def ml_heal_element(page, target_dna: dict) -> str | None:
    """
    Self-healing orchestration for web / mobile-web (Playwright).
    Scrapes current DOM, runs ML, returns healed XPath or None.
    """
    candidates = scrape_dom_web(page)
    logger.info("🧠 ML Engine analyzing %d candidates...", len(candidates))

    winner_dna = _ml_healer.train_and_predict(target_dna, candidates)
    if not winner_dna:
        logger.error("❌ ML Engine could not confidently match an element.")
        return None

    return build_locator_from_dna(winner_dna)


def ml_heal_element_appium(driver, target_dna: dict) -> str | None:
    """
    Self-healing orchestration for native apps (Appium — Android / iOS).
    Scrapes page source XML, runs ML, returns healed locator or None.
    Stub — extend with Appium-specific DOM scraping as needed.
    """
    logger.info("🔍 Appium healing stub — extend for Android/iOS DOM scraping.")
    # TODO: implement Appium page source XML scraping and featurization
    return None
