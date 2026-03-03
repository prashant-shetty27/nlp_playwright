"""
ui/platform/android/device_panel.py — Live Android Device Panel

Displays a real-time screenshot of a connected Android device via Appium.
Used by the Android recorder and live execution view.

Features:
    Live screenshot
        Polls driver.get_screenshot_as_base64() every N ms
        Displayed as NiceGUI ui.image (base64 data URI)
        Refresh rate adjustable (default 1000ms)

    Tap-to-select (recorder mode only)
        Click on displayed screenshot → calculate proportional coordinates
        Query Appium page source XML for element at those bounds
        Returns element dict to on_element_pick callback

    Element bounds overlay
        When an element is selected, draw a highlight rectangle
        Coordinates from element 'bounds' attribute in page source

    Device controls row (below screenshot)
        [Home]    → driver.press_keycode(3)
        [Back]    → driver.press_keycode(4)
        [Recent]  → driver.press_keycode(187)
        [Screenshot] → save current screenshot to data/android/screenshots/

Props:
    driver:         appium.webdriver.Remote — live Appium driver
    refresh_ms:     int (default 1000)
    mode:           "recorder" | "live" (live hides tap-to-select)
    on_element_pick: callable(element: dict) | None

Integration:
    Used in: pages/platform/android/recorder.py
    Also mounted in: pages/executions/live.py (live mode)
"""
