"""
ui/components/screenshot_strip.py — Screenshot Thumbnail Strip

A horizontal scrollable row of screenshot thumbnails from a test run.
Thumbnails update live during execution and are clickable for full-screen view.

Features:
    - Horizontal scroll, fixed height (160px thumbnails)
    - Each thumbnail: step number label + timestamp overlay
    - Click → opens full-screen lightbox with prev/next navigation
    - Failed-step thumbnails get a red border
    - [Download all] button → zips all screenshots

Props:
    screenshots: list[dict]
        Each dict: { path: str, step_index: int, step_text: str, status: str, timestamp: str }
    on_add:     callable | None — called when a new screenshot arrives (live mode)
    height:     str (default "180px")

Live update:
    Bind to execution state; new screenshots appended as steps complete.

Used in: pages/executions/live.py, pages/reports/detail.py
"""
