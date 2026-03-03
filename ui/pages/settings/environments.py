"""
ui/pages/settings/environments.py — Environment Manager  (route: /settings)

Create and manage named environments (local, staging, cloud, etc.)
Each environment defines base URLs and variable overrides per platform.

Layout:
    Left — Environment list
        Each env: name | active indicator | [Set Active] button
        [+ New Environment] button

    Right — Environment editor
        Name input
        Description
        Per-platform base URLs:
            Web base URL      (e.g. https://staging.myapp.com)
            Android app ID    (e.g. com.example.myapp)
            iOS bundle ID     (e.g. com.example.myapp)
        Variable overrides table:
            Key | Value (masked if sensitive) | [+Add]
        [Save] [Delete]

Data source: config/environments.json
    Schema: { env_name: { web_url, android_app_id, ios_bundle_id, vars: {k:v} } }

Integration with config/environment_manager.py
"""
