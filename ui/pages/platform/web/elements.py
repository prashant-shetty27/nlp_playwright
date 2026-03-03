"""
ui/pages/platform/web/elements.py — Web Element Repository  (route: /platform/web/elements)

Manages all web locators captured via the Playwright spy / Chrome extension.

Layout:
    Toolbar
        Search bar (filter by name / locator value)
        Page filter dropdown  (group elements by page/screen name)
        Import button         (bulk import from locators_manual.json)
        Add manually button   → opens element form dialog

    Element table
        Columns:
            Name            friendly identifier (e.g. "login_btn")
            Page/Screen     logical grouping
            Strategy        id | css | xpath | aria-label | name | text
            Locator value   raw selector string
            Last verified   timestamp of last successful use
            Actions         Edit | Delete | Copy selector | Test locator

    Element detail panel (side drawer on row click)
        All attributes captured by spy: tagName, id, name, class, aria-label,
        innerText, custom_xpath, screenshot thumbnail
        Edit locator strategy inline

Data source:
    data/web/elements.json
    (read via locators/manager.py — filter platform=="web")

On save → writes back to data/web/elements.json
On delete → removes entry + shows undo toast
"""
