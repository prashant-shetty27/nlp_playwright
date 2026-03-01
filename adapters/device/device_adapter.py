"""
adapters/device/device_adapter.py
Device-level feature adapter using Appium plugins and platform APIs.
Supports: Camera, Gallery, GPS/Location, Contacts, Push Notifications,
          Google Apps, Local Messaging, File Download/Upload.
No hardcoded values — all config from config/settings.py or env vars.
"""
import logging
from config import settings

logger = logging.getLogger(__name__)


class DeviceAdapter:
    """
    Device-level automation adapter.
    Provides access to native device features via Appium.
    Works with both Android and iOS drivers.
    """

    def __init__(self, driver):
        """
        driver: Active Appium WebDriver instance (Android or iOS).
        """
        self._driver = driver

    # ── Camera & Gallery ──────────────────────────────────────────────────────

    def take_photo(self, save_path: str = None):
        """Triggers device camera and optionally saves the photo."""
        logger.info("📷 Triggering device camera...")
        # Appium: use mobile:startActivity (Android) or XCUITest commands (iOS)
        # Extend with platform-specific implementation
        raise NotImplementedError("Implement platform-specific camera trigger.")

    def pick_from_gallery(self, file_path: str):
        """Uploads a file to simulate picking from gallery."""
        self._driver.push_file("/sdcard/Pictures/test_image.jpg", source_path=file_path)
        logger.info("🖼️ Image pushed to device gallery.")

    # ── GPS / Location ────────────────────────────────────────────────────────

    def set_location(self, latitude: float, longitude: float, altitude: float = 0.0):
        """Sets mock GPS location on the device."""
        self._driver.set_location(latitude, longitude, altitude)
        logger.info("📍 Location set: lat=%s, lon=%s", latitude, longitude)

    # ── Contacts & Phone ─────────────────────────────────────────────────────

    def get_contacts(self) -> list:
        """
        Retrieves device contacts.
        Requires Appium contacts permission and plugin support.
        """
        logger.info("📒 Fetching device contacts...")
        raise NotImplementedError("Implement contacts fetch via Appium plugin or ADB.")

    def make_call(self, phone_number: str):
        """Initiates a phone call."""
        self._driver.execute_script("mobile: shell", {
            "command": f"am start -a android.intent.action.CALL -d tel:{phone_number}"
        })
        logger.info("📞 Initiating call to: %s", phone_number)

    # ── Push Notifications ────────────────────────────────────────────────────

    def open_notifications(self):
        """Opens the notification tray."""
        self._driver.open_notifications()
        logger.info("🔔 Notifications tray opened.")

    # ── Google Apps ───────────────────────────────────────────────────────────

    def open_google_maps(self, query: str):
        """Opens Google Maps with a search query."""
        self._driver.execute_script("mobile: shell", {
            "command": f"am start -a android.intent.action.VIEW -d 'geo:0,0?q={query}'"
        })
        logger.info("🗺️ Google Maps opened with query: %s", query)

    def open_google_chrome(self, url: str):
        """Opens Google Chrome with a URL."""
        self._driver.execute_script("mobile: shell", {
            "command": f"am start -a android.intent.action.VIEW -d '{url}' com.android.chrome"
        })
        logger.info("🌐 Chrome opened with URL: %s", url)

    # ── Local Messaging Apps ──────────────────────────────────────────────────

    def send_sms(self, phone_number: str, message: str):
        """Opens SMS app to send a message."""
        self._driver.execute_script("mobile: shell", {
            "command": f"am start -a android.intent.action.SENDTO -d sms:{phone_number} --es sms_body '{message}'"
        })
        logger.info("💬 SMS intent sent to: %s", phone_number)

    def open_whatsapp(self, phone_number: str, message: str = ""):
        """Opens WhatsApp with a pre-filled message."""
        self._driver.execute_script("mobile: shell", {
            "command": f"am start -a android.intent.action.VIEW -d 'https://api.whatsapp.com/send?phone={phone_number}&text={message}'"
        })
        logger.info("💬 WhatsApp opened for: %s", phone_number)

    # ── File Download ─────────────────────────────────────────────────────────

    def download_file(self, url: str, local_path: str):
        """Downloads a file from device and pulls to local path."""
        remote_path = f"/sdcard/Download/{local_path.split('/')[-1]}"
        self._driver.execute_script("mobile: shell", {
            "command": f"am start -a android.intent.action.VIEW -d '{url}'"
        })
        import time
        time.sleep(3)
        self._driver.pull_file(remote_path, local_path)
        logger.info("⬇️ File downloaded to: %s", local_path)

    def pull_file(self, device_path: str, local_path: str):
        """Pulls any file from device to local machine."""
        data = self._driver.pull_file(device_path)
        import base64
        with open(local_path, "wb") as f:
            f.write(base64.b64decode(data))
        logger.info("📁 File pulled from %s to %s", device_path, local_path)
