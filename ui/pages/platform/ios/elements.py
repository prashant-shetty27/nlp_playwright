"""
ui/pages/platform/ios/elements.py — iOS Element Repository  (route: /platform/ios/elements)

Manages all iOS locators captured via the Appium XCUITest spy.

Layout:
    Toolbar
        Search bar (filter by name / accessibility-id / label)
        Screen filter dropdown  (group by app screen)
        Import button           (bulk import from recorded_elements.json ios entries)
        Add manually button     → opens element form dialog

    Element table
        Columns:
            Name            friendly identifier (e.g. "saerchBarHomePage")
            Screen          app screen name
            Strategy        accessibility-id | label | identifier | xpath | type
            Locator value   raw selector string
            Last verified   timestamp of last successful use
            Actions         Edit | Delete | Copy | Test on device

    Element detail side drawer
        Attributes from XCUITest page source:
            accessibility-id (note typos in real apps preserved),
            label, identifier, type (XCUIElementType*),
            value, enabled, visible, frame (x, y, width, height)
        Screenshot thumbnail of element in context

    Note on iOS quirks:
        Preserve exact accessibility-id values including typos
        (e.g. "saerchBarHomePage" on JustDial app must stay as-is)

Data source:
    data/ios/elements.json
"""
