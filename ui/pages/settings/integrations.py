"""
ui/pages/settings/integrations.py — Integrations  (route: /settings/integrations)

Configure third-party notification and CI/CD integrations.

Layout:
    Integration cards (enabled/disabled toggle on each):

    Slack
        Webhook URL input (masked)
        Channel name
        Events: on failure | on pass | always
        [Test connection] button

    Email
        SMTP host | port | username | password (masked)
        From address | To addresses (comma-separated)
        Events: on failure | on pass | always
        [Send test email] button

    GitHub Actions / CI webhook
        Webhook endpoint URL
        Secret token (masked)
        Trigger: on run complete | on failure only
        [Test webhook] button

    VS Code Snippets sync
        Toggle: auto-sync element names to .vscode/locators.code-snippets
        [Sync now] button → calls reporting/snippet_sync.py

Data source:
    .env — Slack webhook, SMTP credentials (read-only in UI, masked)
    config/controllers.json — NOTIFY_ON_SLACK, NOTIFY_ON_EMAIL toggles
"""
