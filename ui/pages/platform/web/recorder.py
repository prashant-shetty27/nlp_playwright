"""
ui/pages/platform/web/recorder.py — Web Flow Recorder  (route: /platform/web/recorder)

Visual recorder for web automation — migrated and restructured from ui_builder.py.

Panels:
    Left — Browser control
        URL input + [Launch] button  → opens Playwright browser
        Live screenshot of current page (auto-refresh)
        [Inspect Element] toggle     → activates hover-to-select mode
        Element highlight overlay on hover

    Centre — Captured Elements panel
        List of elements picked from the page
        Each entry: element name | tagName | strategy | locator value
        Inline name editor (sanitize_and_match_identifier logic)
        [Save to Library] → persists to data/web/elements.json

    Right — Flow Builder
        Ordered list of recorded steps (drag-to-reorder)
        Each step built from: action + element + optional value
        Action picker: click | type | verify | wait | screenshot | scroll | js click
        [+ Add Step] manually or auto-append on element selection
        [Save Flow] → writes to flows/web/recorded/<name>.flow
        [Run Flow]  → sends to executions/run_center.py

Key logic reused from ui_builder.py:
    sanitize_and_match_identifier()   — normalize + dedup element names
    generate_safe_xpath()             — priority-based locator generation
    persist_element_to_disk()         — thread-safe JSON write with fcntl lock
    read_database_unlocked()          — reads data/web/elements.json
"""
