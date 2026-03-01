"""
healing/healer.py
Backward-compatible shim over core.healer.

Why this exists:
- Preserves legacy imports (`from healing.healer import ml_heal_element`)
- Keeps one authoritative healing implementation in `core.healer`
"""
from playwright.sync_api import Page

from core.healer import (
    build_locator_from_dna,
    ml_heal_element as _ml_heal_element,
    ml_heal_element_appium,
    scrape_dom_web,
)


def scrape_current_dom(page: Page) -> list:
    """Legacy alias for `core.healer.scrape_dom_web`."""
    return scrape_dom_web(page)


def generate_locator(element_dna: dict) -> str:
    """Legacy alias for `core.healer.build_locator_from_dna`."""
    return build_locator_from_dna(element_dna)


def ml_heal_element(page: Page, target_dna: dict) -> str | None:
    """Legacy alias for `core.healer.ml_heal_element`."""
    return _ml_heal_element(page, target_dna)


__all__ = [
    "scrape_current_dom",
    "generate_locator",
    "ml_heal_element",
    "ml_heal_element_appium",
]
