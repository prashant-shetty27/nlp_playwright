"""
ui/pages/plans/index.py — Test Plans list  (route: /plans)

Shows all test plans across platforms. A plan groups suites across
Web / Android / iOS and defines execution order, environment, and profile.

Layout:
    Toolbar
        [+ New Plan] button → /plans/new
        Filter: All | Web | Android | iOS | Multi-platform

    Plans table
        Columns:
            Name            plan display name
            Platforms       badge row: Web | Android | iOS (only included ones shown)
            Suites          count of test suites in plan
            Environment     assigned env (local / staging / cloud)
            Profile         execution profile name
            Last Run        status + timestamp
            Actions         Run now | Edit | Clone | Delete

    Empty state:
        Illustration + "Create your first test plan" CTA

Data source:
    plans/*.json  (parsed by plan_runner.py schema)
    config/environments.json for env names
    config/execution_preferences.json for profile names
"""
