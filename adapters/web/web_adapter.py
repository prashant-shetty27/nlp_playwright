"""
adapters/web/web_adapter.py
Web automation adapter — wraps Playwright for desktop browser automation.
Implements the BaseAdapter interface for unified cross-platform execution.
No hardcoded values — all config loaded from config/settings.py.
"""
import logging
from adapters.base_adapter import BaseAdapter
from core.healer import ml_heal_element
from core.registry import codeless_snippet
from locators.manager import get_locator_and_dna
from nlp.variable_manager import resolve_variables
from execution.browser_manager import open_browser, close_browser
from execution.session import TestSession
from execution.retry import with_retry
from config import settings

logger = logging.getLogger(__name__)


class WebAdapter(BaseAdapter):
    """
    Web automation adapter using Playwright.
    Supports desktop browsers: Chromium, Firefox, WebKit.
    """

    platform = "web"

    def __init__(self):
        self._page = None
        self._session = None

    def launch(self, **kwargs):
        """Launches a desktop browser session."""
        self._session = TestSession()
        self._page = open_browser(self._session)
        logger.info("🌐 Web adapter launched.")
        return self._page

    def quit(self, label: str = "web_session"):
        """Closes the browser session."""
        if self._page:
            close_browser(self._page, label, self._session)
            self._page = None
            self._session = None
            logger.info("🌐 Web adapter closed.")

    def navigate(self, url: str):
        """Navigates to a URL."""
        resolved = resolve_variables(url)
        self._page.goto(resolved)

    def click(self, locator_name: str):
        """Clicks an element by locator name."""
        xpath, dna = get_locator_and_dna(locator_name)
        if not xpath:
            raise Exception(f"Locator '{locator_name}' not found.")
        xpath = resolve_variables(xpath)
        loc = self._page.locator(xpath).first
        if not loc.is_visible(timeout=settings.ACTION_TIMEOUT_MS):
            healed = ml_heal_element(self._page, dna)
            if healed:
                loc = self._page.locator(healed).first
        loc.click()

    def fill(self, locator_name: str, text: str):
        """Types text into an input field."""
        xpath, dna = get_locator_and_dna(locator_name)
        if not xpath:
            raise Exception(f"Locator '{locator_name}' not found.")
        xpath = resolve_variables(xpath)
        self._page.locator(xpath).first.fill(resolve_variables(text))

    def get_page(self):
        """Returns the raw Playwright page object."""
        return self._page
