"""
ui/components/data_source_picker.py — Data Source Picker

Inline dropdown to bind a flow step's value to an external data source
instead of a hardcoded string. Used alongside nlp_input.py in step editors.

Example use case:
    Step: type "<username>" in login_field
    Instead of hardcoding "admin@test.com", bind to:
        → Excel row: datasets/users.xlsx, column "email", row 1
        → Variable:  ${username} from data/common/variables.json
        → Faker:     faker.email()
        → API:       GET /api/users[0].email

Picker triggers via [⊕] icon next to an input value in a step row.

Data source options:
    Excel / CSV     → file picker → column selector → row index
    JSON dataset    → file picker → JSON path (e.g. [0].email)
    Variable        → searchable dropdown of defined ${variables}
    Faker           → category picker (name, email, phone, address, date, etc.)
    API response    → URL input + JSON path to extract value

Output:
    Returns a data-binding token that runner resolves at runtime:
        ${dataset:users.xlsx:email:1}
        ${var:username}
        ${faker:email}
        ${api:GET:https://...:$.users[0].email}

Used in: pages/platform/*/test_cases.py step editor
"""
