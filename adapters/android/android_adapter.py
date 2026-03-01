"""
adapters/android/android_adapter.py
Android native app automation adapter using Appium.
Supports native Android apps, hybrid apps, and Chrome mobile browser.
No hardcoded values — all device/app config from config/settings.py or env vars.
"""
import logging
from adapters.base_adapter import BaseAdapter
from config import settings

logger = logging.getLogger(__name__)


class AndroidAdapter(BaseAdapter):
    """
    Android adapter using Appium WebDriver.
    Supports: native apps, hybrid apps, Chrome mobile browser.
    Requires: Appium server running, Android device/emulator connected.
    """

    platform = "android"

    def __init__(self):
        self._driver = None

    def launch(self, capabilities: dict = None, **kwargs):
        """
        Launches an Appium session for Android.
        capabilities: Appium desired capabilities dict.
                      If not provided, loaded from config/settings.py (ANDROID_CAPABILITIES).
        """
        try:
            from appium import webdriver as appium_driver
        except ImportError:
            raise ImportError(
                "Appium Python client not installed. Run: pip install Appium-Python-Client"
            )

        caps = capabilities or settings.ANDROID_CAPABILITIES
        if not caps:
            raise ValueError(
                "Android capabilities not configured. "
                "Set ANDROID_CAPABILITIES in config/settings.py or provide via argument."
            )

        server_url = settings.APPIUM_SERVER_URL
        self._driver = appium_driver.Remote(server_url, caps)
        logger.info("🤖 Android adapter launched. Device: %s", caps.get("deviceName", "unknown"))
        return self._driver

    def quit(self, label: str = "android_session"):
        if self._driver:
            self._driver.quit()
            self._driver = None
            logger.info("🤖 Android adapter closed.")

    def navigate(self, url: str):
        """Navigate to a URL (for hybrid/browser contexts)."""
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

    def tap(self, x: int, y: int):
        """Tap at screen coordinates."""
        # Appium 2+: prefer W3C/mobile gesture APIs over deprecated TouchAction.
        self._driver.execute_script(
            "mobile: clickGesture",
            {"x": int(x), "y": int(y)},
        )

    def swipe(self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int = 500):
        """Swipe gesture."""
        self._driver.swipe(start_x, start_y, end_x, end_y, duration)

    def get_driver(self):
        return self._driver
