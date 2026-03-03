"""
ui/pages/platform/web/test_cases.py — Web Test Cases  (route: /platform/web)

List and editor for web NLP flow test cases.

Layout:
    Left panel — Test case list
        Search / filter bar
        Sort: name | last modified | status
        Each row: test case name | step count | last run status | modified date
        [+ New Test Case] button at bottom

    Right panel — NLP Step Editor (opens when a test case is selected)
        Header: test case name (editable), tags, description
        Step list (ordered, drag-to-reorder)
            Each step: step number | NLP text | action type badge | target element
            Inline edit: click step text to edit, element picker autocomplete
        Toolbar
            [+ Add Step]  → appends new step row with nlp_input.py autocomplete
            [Run]         → sends to executions/run_center.py with this flow pre-loaded
            [Save]        → serialises steps to flows/web/manual/<name>.flow
            [Record]      → opens /platform/web/recorder in side panel

    Supported NLP step actions (auto-suggested via nlp_input.py):
        go to url, click, type, verify text, wait, take screenshot,
        js click, js type, hover, scroll, select option, upload file,
        assert url, assert title, press key, store value

Data source:
    flows/web/manual/*.flow    — hand-written
    flows/web/recorded/*.flow  — recorded via Playwright spy
"""
