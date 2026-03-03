"""
ui/pages/platform/android/recorder.py — Android Flow Recorder  (route: /platform/android/recorder)

Visual recorder for Android automation — restructured from recorder_ui.py (android branch).

Panels:
    Left — Device panel (platform/android/device_panel.py)
        Live device screenshot (auto-refresh every 2s via Appium)
        Tap-to-select: click on screenshot → maps pixel to element from page source
        Element highlight overlay on selection
        Device controls: Home | Back | App switcher

    Centre — Captured Elements panel
        Parsed from Appium XML page source (UiAutomator2)
        Attributes extracted: resource-id, content-desc, text, hint, class, bounds
        Best locator selected via _best_locator(): resource-id > content-desc > text > xpath
        Inline name editor (dedup against data/android/elements.json)
        [Save to Library] → persists to data/android/elements.json

    Right — Flow Builder
        Ordered step list (drag-to-reorder)
        Action picker: tap | type | verify | swipe | wait | screenshot |
                       dismiss alerts | press back | press home | press enter
        [Save Flow] → flows/android/recorded/<name>.flow
        [Run Flow]  → executions/run_center.py --platform android

Key logic reused from recorder_ui.py:
    parse_appium_element()    — XML node → element dict
    _best_locator()           — platform-specific locator strategy
    _element_screenshot()     — highlight selected element
    _save_flow()              — serialize steps to .flow format
    _refresh_screenshot()     — poll device, update live panel
"""
