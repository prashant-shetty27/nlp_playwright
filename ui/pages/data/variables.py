"""
ui/pages/data/variables.py — Runtime Variables  (route: /data/variables)

Manage global and environment-scoped runtime variables available in all NLP flows
via ${variable_name} syntax. Shared across all platforms.

Variable scopes:
    Global      — available in every flow on every platform
    Environment — overrides global when a specific env is active (local/staging/cloud)
    Flow-local  — set within a flow via 'store value' step (not editable here, shown read-only)

Layout:
    Scope tabs: Global | Per-Environment

    Variables table
        Columns:
            Name        ${variable_name} format
            Value       masked if sensitive (toggle eye icon to reveal)
            Scope       Global / env name
            Type        string | number | boolean | secret
            Actions     Edit | Delete

    [+ Add Variable] button → inline form row:
        Name input | Value input | Scope selector | Type selector | [Sensitive] toggle

    Import / Export
        [Import from .env] → parses current .env file, imports as global secrets
        [Export as .env]   → downloads variables as .env format

Data source: data/common/variables.json
    Schema: { "global": {key: {value, type, sensitive}}, "env": {env_name: {key: ...}} }

Integration with nlp/variable_manager.py:
    Reads RUNTIME_VARIABLES at run time
    resolve_variables() replaces ${key} with values from this store
"""
