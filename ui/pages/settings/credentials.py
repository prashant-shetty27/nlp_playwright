"""
ui/pages/settings/credentials.py — Credentials Editor  (route: /settings/credentials)

View and edit project credentials stored in the .env file.
Values are always masked in the UI — never logged or shown in plain text.

Layout:
    Warning banner: "Changes here write directly to your .env file"

    Credentials table
        Each row: Key | Masked value (●●●●●) | [Toggle reveal] | [Edit] | [Delete]
        Rows grouped by category (auto-detected from key prefix):
            APPIUM_*    — Appium server settings
            SLACK_*     — Slack integration
            SMTP_*      — Email settings
            DB_*        — Database (if any)
            Other       — everything else

    [+ Add credential] → inline form: Key input | Value input (password type) | [Save]

    [Export .env.example] → downloads a .env.example with keys but empty values
    [Import .env] → parse and merge a .env file (warns on key conflicts)

Security rules:
    - Values never appear in logs, page source, or API responses
    - Reveal toggle only works for the current browser session
    - No credential is ever committed to git (.gitignore enforced)

Data source: .env (read/write via python-dotenv)
"""
