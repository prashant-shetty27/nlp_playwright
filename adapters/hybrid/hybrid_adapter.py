"""
adapters/hybrid/hybrid_adapter.py
Hybrid app automation adapter using Appium with context switching.
Supports apps built with Cordova, Ionic, React Native WebView, etc.
Switches between NATIVE_APP and WEBVIEW contexts seamlessly.
No hardcoded values — all config from config/settings.py or env vars.
"""
import logging
from adapters.base_adapter import BaseAdapter
from config import settings

logger = logging.getLogger(__name__)


class HybridAdapter(BaseAdapter):
    """
    Hybrid app adapter using Appium context switching.
    Automatically switches between native and webview contexts.
    Works for: Cordova, Ionic, React Native WebView, Flutter WebView.
    """

    platform = "hybrid"

    def __init__(self):
        self._driver = None
        self._current_context = "NATIVE_APP"

    def launch(self, capabilities: dict = None, **kwargs):
        """
        Launches an Appium session for a hybrid app.
        capabilities: Appium desired capabilities dict.
                      If not provided, loaded from config/settings.py (HYBRID_CAPABILITIES).
        """
        try:
            from appium import webdriver as appium_driver
        except ImportError:
            raise ImportError(
                "Appium Python client not installed. Run: pip install Appium-Python-Client"
            )

        caps = capabilities or settings.HYBRID_CAPABILITIES
        if not caps:
            raise ValueError(
                "Hybrid capabilities not configured. "
                "Set HYBRID_CAPABILITIES in config/settings.py or provide via argument."
            )

        server_url = settings.APPIUM_SERVER_URL
        self._driver = appium_driver.Remote(server_url, caps)
        logger.info("🔀 Hybrid adapter launched.")
        return self._driver

    def quit(self, label: str = "hybrid_session"):
        if self._driver:
            self._driver.quit()
            self._driver = None
            logger.info("🔀 Hybrid adapter closed.")

    def switch_to_webview(self, webview_index: int = 0):
        """Switch to WebView context for web-based interactions."""
        contexts = self._driver.contexts
        webviews = [c for c in contexts if "WEBVIEW" in c]
        if not webviews:
            raise Exception("No WebView context found in the hybrid app.")
        target = webviews[webview_index]
        self._driver.switch_to.context(target)
        self._current_context = target
        logger.info("🔀 Switched to WebView context: %s", target)

    def switch_to_native(self):
        """Switch back to native app context."""
        self._driver.switch_to.context("NATIVE_APP")
        self._current_context = "NATIVE_APP"
        logger.info("🔀 Switched to NATIVE_APP context.")

    def navigate(self, url: str):
        """Navigate to a URL inside a WebView context."""
        self.switch_to_webview()
        self._driver.get(url)

    def click(self, locator_name: str):
        """Click element — works in both native and webview contexts."""
        from appium.webdriver.common.appiumby import AppiumBy
        element = self._driver.find_element(AppiumBy.XPATH, locator_name)
        element.click()

    def fill(self, locator_name: str, text: str):
        """Type text into an input element."""
        from appium.webdriver.common.appiumby import AppiumBy
        element = self._driver.find_element(AppiumBy.XPATH, locator_name)
        element.clear()
        element.send_keys(text)

    def get_driver(self):
        return self._driver
