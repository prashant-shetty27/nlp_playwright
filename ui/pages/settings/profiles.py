"""
ui/pages/settings/profiles.py — Execution Profiles  (route: /settings/profiles)

Save and manage named execution profiles (headless, capture settings, retry logic).

Layout:
    Left — Profile list
        Each profile: name | last used indicator
        [+ New Profile] button

    Right — Profile editor
        Name input
        Browser / device settings:
            Headless toggle
            Slow motion (ms) slider
            Browser: chromium | firefox | webkit
        Capture settings:
            Screenshots toggle
            Video recording toggle
            Log level: debug | info | warning
        Retry settings:
            Retry on failure toggle
            Max retries (1–5)
            Retry delay (ms)
        Timeouts:
            Step timeout (ms)
            Session timeout (ms)
            Idle watchdog interval (ms)
        [Set as default] [Save] [Delete]

Data source: config/execution_preferences.json
Integration with config/execution_preferences.py:
    save_profile() / get_profile() / get_last_used_profile_name()
"""
