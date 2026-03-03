"""
ui/platform/ios/device_panel.py — Live iOS Device Panel

Displays a real-time screenshot of a connected iOS device via Appium XCUITest.
Used by the iOS recorder and live execution view.

Features:
    Live screenshot
        Polls driver.get_screenshot_as_base64() every N ms
        Displayed as NiceGUI ui.image (portrait orientation, scaled to fit)
        Refresh rate: 1000ms default (XCUITest screenshot is slower than Android)

    Tap-to-select (recorder mode only)
        Click on screenshot → map pixel to device coordinates (accounting for scale)
        Parse XCUITest page source XML for element at those bounds
        frame attribute: x="N" y="N" width="N" height="N"
        Returns element dict to on_element_pick callback

    Element bounds overlay
        Highlight rectangle drawn over selected element's frame

    Device controls row
        [Home]       → driver.execute_script("mobile: pressButton", {"name": "home"})
        [Lock]       → driver.lock()
        [Screenshot] → save to data/ios/screenshots/

iOS-specific notes:
    - Screenshots may include notch/dynamic island area — scale accordingly
    - Page source can be 100KB+ (122KB observed for JustDial home) — parse lazily
    - Coordinate scaling: device logical points ≠ screenshot pixels (check scale factor)
    - Contacts/system dialogs (SpringBoard) appear in page source but cannot be tapped
      via Appium — user must dismiss manually; show warning banner when detected

Props:
    driver:          appium.webdriver.Remote — live XCUITest driver
    refresh_ms:      int (default 1000)
    mode:            "recorder" | "live"
    on_element_pick: callable(element: dict) | None

Integration:
    Used in: pages/platform/ios/recorder.py
    Also mounted in: pages/executions/live.py (live mode)
"""
