"""
ui/components/element_card.py — Element Card

A card widget showing a single element's name, locator strategy, and value.
Used in element repository pages and recorder panels.

Visual layout:
    ┌─────────────────────────────────────┐
    │ 🔵 login_button          [Web]      │
    │ Strategy: id                        │
    │ Locator:  #login-btn                │
    │ Screen:   Login Page                │
    │ [Edit]  [Copy]  [Test]  [Delete]    │
    └─────────────────────────────────────┘

Props:
    name:       str — element identifier
    platform:   str — "web" | "android" | "ios"
    strategy:   str — "id" | "css" | "xpath" | "accessibility-id" | "resource-id" | etc.
    locator:    str — raw selector string
    screen:     str — page/screen name
    on_edit:    callable
    on_delete:  callable
    on_copy:    callable — copies locator to clipboard

Platform dot color matches theme.PLATFORM_COLOR.

Used in: pages/platform/*/elements.py, pages/platform/*/recorder.py
"""
