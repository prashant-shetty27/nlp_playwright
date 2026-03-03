"""
ui/pages/platform/ios/test_cases.py — iOS Test Cases  (route: /platform/ios)

List and editor for iOS NLP flow test cases (Appium XCUITest).

Layout:
    Left panel — Test case list
        Search / filter bar
        Each row: test case name | step count | last run status | modified date
        [+ New Test Case] button

    Right panel — NLP Step Editor
        Step list (ordered, drag-to-reorder)
        Element autocomplete from data/ios/elements.json

        Supported NLP step actions (iOS-specific, auto-suggested):
            tap <element>, type "<text>" in <element>, tap text "<label>",
            verify text "<text>", swipe left | right | up | down,
            press enter, press home, hide keyboard, wait <n> seconds,
            dismiss alerts, take screenshot, scroll to <element>,
            store value from <element>

        iOS-incompatible actions are hidden/disabled:
            press back → not applicable on iOS (use swipe or nav bar tap)
            mobile: pressKey with keycode → Android only

        Toolbar
            [+ Add Step]
            [Run]         → run_center.py with --platform ios
            [Save]        → flows/ios/manual/<name>.flow
            [Record]      → opens /platform/ios/recorder

Data source:
    flows/ios/manual/*.flow
    flows/ios/recorded/*.flow
"""
