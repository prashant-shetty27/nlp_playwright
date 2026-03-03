"""
ui/pages/platform/android/elements.py — Android Element Repository  (route: /platform/android/elements)

Manages all Android locators captured via the Appium spy.

Layout:
    Toolbar
        Search bar (filter by name / locator value / content-desc)
        Screen filter dropdown  (group by app screen/activity)
        Import button           (bulk import from recorded_elements.json android entries)
        Add manually button     → opens element form dialog

    Element table
        Columns:
            Name            friendly identifier (e.g. "search_field")
            Screen          app screen / activity name
            Strategy        resource-id | content-desc | text | class | xpath
            Locator value   raw selector string
            Last verified   timestamp of last successful use
            Actions         Edit | Delete | Copy | Test on device

    Element detail side drawer
        Attributes from Appium page source:
            resource-id, content-desc, text, hint, class, bounds, checkable,
            clickable, enabled, focusable, scrollable
        Screenshot thumbnail of element in context

Data source:
    data/android/elements.json
    (filtered from recorded_elements.json platform=="android" entries)
"""
