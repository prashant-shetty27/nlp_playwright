"""
ui/pages/platform/ios/recorder.py — iOS Flow Recorder  (route: /platform/ios/recorder)

Visual recorder for iOS automation — restructured from recorder_ui.py (ios branch).

Panels:
    Left — Device panel (platform/ios/device_panel.py)
        Live device screenshot (auto-refresh every 2s via Appium XCUITest)
        Tap-to-select: maps pixel coordinates to XCUITest element
        Element highlight overlay
        Device controls: Home button | Lock | Rotate

    Centre — Captured Elements panel
        Parsed from XCUITest page source XML
        Attributes: accessibility-id, label, identifier, type, value, frame
        Best locator: accessibility-id > label > identifier > xpath
        Inline name editor — preserves exact accessibility-id strings (typos included)
        [Save to Library] → data/ios/elements.json

    Right — Flow Builder
        Action picker: tap | type | verify | swipe | wait | screenshot |
                       dismiss alerts | press enter | press home | hide keyboard
        [Save Flow] → flows/ios/recorded/<name>.flow
        [Run Flow]  → executions/run_center.py --platform ios

iOS-specific notes carried from recorder_ui.py:
    - mobile: clickGesture not available → use mobile: tap or driver.tap()
    - press_enter uses mobile: performIoHidEvent {"page":0x07,"usage":0x28,"durationSeconds":0.005}
    - dismiss alerts uses mobile: alert API (UIAlertController, not SpringBoard dialogs)
    - Capability serialization: use set_capability() not setattr for unknown caps

Key logic reused from recorder_ui.py:
    parse_appium_element()  — XCUITest XML → element dict
    _best_locator()         — ios locator strategy
    _save_flow()            — serialize to .flow format
    _refresh_screenshot()   — poll device screenshot
"""
