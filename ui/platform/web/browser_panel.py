"""
ui/platform/web/browser_panel.py — Live Browser Panel (Web)

Embeds a live view of the Playwright-controlled browser page inside the UI.
Used by the web recorder and live execution view.

Two rendering modes:
    Screenshot mode (default)
        Polls Playwright page.screenshot() every N ms
        Displays as NiceGUI ui.image, refreshed in-place
        Hover-to-highlight: overlay element bounds from page.evaluate()
        Click-to-select: maps click coords back to DOM element via CDP

    Iframe mode (optional, same-origin only)
        Renders an <iframe> pointed at the running Playwright page's URL
        Faster perceived responsiveness, but blocked for cross-origin pages

Props:
    page:           playwright.async_api.Page — live page reference
    refresh_ms:     int — screenshot poll interval (default 500)
    mode:           "screenshot" | "iframe" (default "screenshot")
    on_element_pick: callable(element_dna: dict) — called when user clicks element
    height:         str (default "600px")

Integration:
    Used in: pages/platform/web/recorder.py
    Also mounted in: pages/executions/live.py (screenshot-only, no picking)
"""
