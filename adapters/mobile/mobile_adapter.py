"""
adapters/mobile/mobile_adapter.py
Mobile web adapter — Playwright with mobile device emulation.
Emulates real mobile devices (viewport, user-agent, touch) from config.
No hardcoded values — device profiles loaded from config/settings.py.
"""
import logging
from playwright.sync_api import sync_playwright
from adapters.base_adapter import BaseAdapter
from core.healer import ml_heal_element
from locators.manager import get_locator_and_dna
from nlp.variable_manager import resolve_variables
from config import settings

logger = logging.getLogger(__name__)


class MobileAdapter(BaseAdapter):
    """
    Mobile web adapter using Playwright device emulation.
    Simulates real mobile browsers (Android Chrome, iPhone Safari, etc.)
    """

    platform = "mobile"

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def launch(self, device_name: str = None, **kwargs):
        """
        Launches a mobile-emulated browser session.
        device_name: Playwright device descriptor (e.g. "iPhone 14", "Pixel 7")
                     Loaded from config if not provided.
        """
        device_name = device_name or settings.MOBILE_DEVICE_EMULATION
        self._playwright = sync_playwright().start()
        browser_type = getattr(self._playwright, settings.MOBILE_BROWSER_TYPE or "chromium")
        self._browser = browser_type.launch(headless=settings.HEADLESS)

        device_config = {}
        if device_name and device_name in self._playwright.devices:
            device_config = self._playwright.devices[device_name]
            logger.info("📱 Mobile adapter launched with device: %s", device_name)
        else:
            # Fallback: generic mobile viewport
            device_config = {
                "viewport": {"width": 390, "height": 844},
                "user_agent": settings.MOBILE_USER_AGENT or (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
                ),
                "is_mobile": True,
                "has_touch": True,
            }
            logger.info("📱 Mobile adapter launched with generic mobile viewport.")

        self._context = self._browser.new_context(**device_config)
        self._page = self._context.new_page()
        return self._page

    def quit(self, label: str = "mobile_session"):
        """Closes the mobile browser session."""
        if self._page:
            try:
                self._page.screenshot(
                    path=f"{settings.SCREENSHOTS_DIR}/{label}_end.png"
                )
            except Exception:
                pass
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        logger.info("📱 Mobile adapter closed.")

    def navigate(self, url: str):
        resolved = resolve_variables(url)
        self._page.goto(resolved)

    def click(self, locator_name: str):
        xpath, dna = get_locator_and_dna(locator_name)
        if not xpath:
            raise Exception(f"Locator '{locator_name}' not found.")
        xpath = resolve_variables(xpath)
        loc = self._page.locator(xpath).first
        if not loc.is_visible(timeout=settings.ACTION_TIMEOUT_MS):
            healed = ml_heal_element(self._page, dna)
            if healed:
                loc = self._page.locator(healed).first
        loc.tap()

    def fill(self, locator_name: str, text: str):
        xpath, _ = get_locator_and_dna(locator_name)
        if not xpath:
            raise Exception(f"Locator '{locator_name}' not found.")
        self._page.locator(resolve_variables(xpath)).first.fill(resolve_variables(text))

    def get_page(self):
        return self._page
