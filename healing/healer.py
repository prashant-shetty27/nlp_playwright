"""
healing/healer.py
ML-powered self-healing orchestrator.
Moved from root healer.py — imports locator_builder for separation of concerns.
"""
import logging
from playwright.sync_api import Page

from core.ml_engine import LocatorHealer
from healing.locator_builder import generate_locator

logger = logging.getLogger(__name__)

# Initialize the ML model once (module-level) to avoid repeated memory allocation
_ml_healer = LocatorHealer()


def scrape_current_dom(page: Page) -> list:
    """
    Injects JavaScript to harvest the DNA of every visible element on the page.
    Filters out invisible (0-width / 0-height) elements — reduces DOM noise by ~70%.
    """
    logger.info("🔍 Scraping current DOM for ML candidates...")

    js_payload = """
    () => {
        const elements = Array.from(document.querySelectorAll('*'));

        const candidates = elements.map(el => {
            const rect = el.getBoundingClientRect();

            // Skip invisible elements — user can't interact with them
            if (rect.width === 0 || rect.height === 0) return null;

            let attributesData = {};
            for (let i = 0; i < el.attributes.length; i++) {
                let attr = el.attributes[i];
                if (attr.value && attr.value.length < 300) {
                    attributesData[attr.name] = attr.value;
                }
            }

            return {
                tagName:   el.tagName.toLowerCase(),
                className: el.className || null,
                innerText: el.innerText ? el.innerText.substring(0, 100).trim() : null,
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


def ml_heal_element(page: Page, target_dna: dict) -> str | None:
    """
    Self-healing orchestration:
      1. Scrape the broken page DOM
      2. Feed target DNA + candidates to the ML engine
      3. Convert the winner back to a locator string

    Returns a locator string or None if no safe heal was found.
    """
    current_page_elements = scrape_current_dom(page)
    logger.info("🧠 ML Engine analyzing %d candidate elements...", len(current_page_elements))

    winner_dna = _ml_healer.train_and_predict(target_dna, current_page_elements)

    if not winner_dna:
        logger.error("❌ ML Engine could not confidently match an element.")
        return None

    return generate_locator(winner_dna)
