"""
ui/pages/platform/android/test_cases.py — Android Test Cases  (route: /platform/android)

List and editor for Android NLP flow test cases (Appium).

Layout:
    Left panel — Test case list
        Search / filter bar
        Each row: test case name | step count | last run status | modified date
        [+ New Test Case] button

    Right panel — NLP Step Editor
        Step list (ordered, drag-to-reorder)
        Each step: step number | NLP text | action type badge | element target
        Inline edit with element autocomplete (from data/android/elements.json)

        Supported NLP step actions (Android-specific, auto-suggested):
            tap <element>, type "<text>" in <element>, tap text "<label>",
            verify text "<text>", swipe left | right | up | down,
            press back, press home, press enter, wait <n> seconds,
            dismiss alerts, take screenshot, scroll to <element>,
            long press <element>, store value from <element>

        Toolbar
            [+ Add Step]  → appends step with NLP autocomplete
            [Run]         → run_center.py with --platform android
            [Save]        → flows/android/manual/<name>.flow
            [Record]      → opens /platform/android/recorder

Data source:
    flows/android/manual/*.flow
    flows/android/recorded/*.flow
"""
