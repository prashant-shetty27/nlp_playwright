"""
ui/pages/executions/run_center.py — Run Center  (route: /run)

Launch panel for starting a test run. Accepts pre-filled context from
test_cases.py or plans/index.py via query params (?flow=..., ?plan=..., ?platform=...).

Layout:
    Left — What to run
        Toggle: Single Flow | Test Plan
        Single Flow mode:
            Platform selector (Web / Android / iOS)
            Flow file picker (searchable dropdown from flows/<platform>/**/)
        Test Plan mode:
            Plan selector (from plans/*.json)

    Right — How to run it
        Environment selector   → config/environments.json
        Profile selector       → config/execution_preferences.json
            Inline overrides (headless toggle, capture toggle, retry count)
        [Save as profile] checkbox
        Device selector (Android/iOS only):
            UDID input / dropdown of connected devices
            Platform version input

    Footer
        [Run Now] → invokes runner.py / runner_appium.py as subprocess,
                    redirects to /run/live with process ID

Subprocess commands generated:
    Web:     python runner.py <flow> --env <env> --profile <profile>
    Android: python runner_appium.py <flow> --platform android --profile <profile>
    iOS:     python runner_appium.py <flow> --platform ios --profile <profile>
    Plan:    python plan_runner.py <plan> --profile <profile>
"""
