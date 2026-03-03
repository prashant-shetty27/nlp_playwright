"""
ui/pages/data/datasets.py — Test Datasets  (route: /data)

Upload and manage external data sources used across all platforms.
Data here is shared — not platform-specific.

Layout:
    Toolbar
        [Upload] button → file picker (accepts .xlsx, .csv, .json)
        [Connect API] button → enter URL + auth for live API data source

    Dataset list
        Each card shows:
            File icon (Excel / CSV / JSON / API)
            File name + size + upload date
            Row count (parsed on upload)
            Actions: Preview | Use in flow | Download | Delete

    Dataset preview panel (opens on click)
        Paginated table view of rows and columns
        Column types auto-detected (string / number / date / boolean)
        [Map to variable] → links column to a RUNTIME_VARIABLE name
        [Use as data source] → inserts data-driven step in active flow editor

Supported file types:
    .xlsx, .xls    — parsed via openpyxl
    .csv           — parsed via csv module
    .json          — parsed as list of objects
    API URL        — fetched at runtime, response mapped to variables

Storage: data/common/datasets/
Variable mapping saved in: data/common/variables.json (dataset_mappings key)
"""
