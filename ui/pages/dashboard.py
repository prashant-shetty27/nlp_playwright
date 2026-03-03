"""
ui/pages/dashboard.py — Home / Dashboard  (route: /)

The landing page after login. Provides an at-a-glance overview of the project.

Layout (top → bottom):
    Stats row
        • Total test cases  (web + android + ios combined)
        • Last run status   (PASS / FAIL / RUNNING)
        • Element count     (per-platform breakdown)
        • Open plans        (active test plans)

    Recent Runs table
        Columns: Plan name | Platform | Status | Duration | Timestamp | Actions
        Actions: View report, Re-run

    Quick-action cards
        • Record Web flow   → /platform/web/recorder
        • Record Android    → /platform/android/recorder
        • Record iOS        → /platform/ios/recorder
        • New Test Plan     → /plans/new

    Platform health strip
        Compact row: Web (N tests, last run), Android (N), iOS (N)

Data sources:
    - data/web/elements.json, data/android/elements.json, data/ios/elements.json
    - data/*/logs/ for recent run summaries
    - plans/*.json for active plans
"""
