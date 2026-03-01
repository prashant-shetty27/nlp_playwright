"""
adapters/ios/ios_adapter.py
iOS native app automation adapter using Appium + XCUITest.
Supports native iOS apps, hybrid apps, and Safari mobile browser.
No hardcoded values — all device/app config from config/settings.py or env vars.
"""
import logging
from adapters.base_adapter import BaseAdapter
from config import settings

logger = logging.getLogger(__name__)


class IOSAdapter(BaseAdapter):
    """
    iOS adapter using Appium WebDriver + XCUITest driver.
    Supports: native iOS apps, hybrid apps, Safari mobile.
    Requires: Appium server, Xcode, iOS device or simulator.
    """

    platform = "ios"

    def __init__(self):
        self._driver = None

    def launch(self, capabilities: dict = None, **kwargs):
        """
        Launches an Appium session for iOS.
        capabilities: Appium desired capabilities dict.
                      If not provided, loaded from config/settings.py (IOS_CAPABILITIES).
        """
        try:
            from appium import webdriver as appium_driver
            from appium.options.common.base import AppiumOptions
        except ImportError:
            raise ImportError(
                "Appium Python client not installed. Run: pip install Appium-Python-Client"
            )

        caps = capabilities or settings.IOS_CAPABILITIES
        if not caps:
            raise ValueError(
                "iOS capabilities not configured. "
                "Set IOS_CAPABILITIES in config/settings.py or provide via argument."
            )

        options = AppiumOptions().load_capabilities(caps)
        server_url = settings.APPIUM_SERVER_URL
        self._driver = appium_driver.Remote(server_url, options=options)
        logger.info("🍎 iOS adapter launched. Device: %s", caps.get("deviceName", "unknown"))
        return self._driver

    def quit(self, label: str = "ios_session"):
        if self._driver:
            self._driver.quit()
            self._driver = None
            logger.info("🍎 iOS adapter closed.")

    def navigate(self, url: str):
        """Navigate to a URL (for hybrid/Safari contexts)."""
        self._driver.get(url)

    def click(self, locator_name: str):
        """Click element using Appium locator strategies."""
        from appium.webdriver.common.appiumby import AppiumBy
        element = self._driver.find_element(AppiumBy.XPATH, locator_name)
        element.click()

    def fill(self, locator_name: str, text: str):
        """Type text into an input element."""
        from appium.webdriver.common.appiumby import AppiumBy
        element = self._driver.find_element(AppiumBy.XPATH, locator_name)
        element.clear()
        element.send_keys(text)

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 500):
        """Swipe gesture."""
        self._driver.swipe(start_x, start_y, end_x, end_y, duration)

    def get_driver(self):
        return self._driver
