"""
ui/pages/plans/editor.py — Test Plan Editor  (route: /plans/new, /plans/<id>/edit)

Create or edit a test plan that can span multiple platforms and suites.

Layout (wizard-style tabs):
    Tab 1 — Basics
        Plan name input
        Description textarea
        Tags input

    Tab 2 — Test Suites
        Per-platform accordion:
            Web section
                Searchable list of web flows (flows/web/**/*.flow)
                Check to include → added to plan's web suite
            Android section
                Searchable list of android flows
            iOS section
                Searchable list of ios flows
        Execution order: parallel | sequential toggle per platform
        Dependency arrows (run web THEN android, etc.)

    Tab 3 — Environment & Profile
        Environment selector → from config/environments.json
            Shows env vars preview (base URL, credentials masked)
        Profile selector → from config/execution_preferences.json
            Shows profile summary: headless, capture, retry count, timeouts

    Tab 4 — Schedule (optional)
        One-time / recurring toggle
        Cron expression builder
        Notification targets: Slack channel, email list

    Footer
        [Cancel]  [Save Draft]  [Save & Run]

On Save:
    Writes to plans/<name>.plan.json (plan_runner.py compatible schema)
"""
