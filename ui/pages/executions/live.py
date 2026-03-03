"""
ui/pages/executions/live.py — Live Execution View  (route: /run/live)

Real-time view while a test run is in progress. Streams output from
the runner subprocess via WebSocket / async polling.

Layout:
    Top bar
        Run ID | Platform badge | Flow/Plan name | Start time | [Stop Run] button

    Left — Step Progress list
        Ordered list of NLP steps from the flow
        Each step shows:
            Status icon: ⏳ pending | ▶ running | ✅ pass | ❌ fail | ⏭ skipped
            Step text (NLP command)
            Duration (elapsed when done)
            Expand to see: action type, element resolved, locator used, error message

    Right — Live panels (tabbed)
        Tab 1: Device / Browser
            Web:     Live Playwright screenshot (platform/web/browser_panel.py)
            Android: Live Appium screenshot (platform/android/device_panel.py)
            iOS:     Live Appium screenshot (platform/ios/device_panel.py)
        Tab 2: Log output
            Real-time scrollable log (components/log_viewer.py)
            Autoscroll toggle
        Tab 3: Screenshots
            Thumbnail strip updating as screenshots are captured
            (components/screenshot_strip.py)

    Bottom bar
        Progress bar: N/Total steps | Status: RUNNING | Elapsed: HH:MM:SS

On completion → shows summary toast + [View Report] button → /reports/<run_id>
"""
