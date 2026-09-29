#!/bin/bash
# Runs the CLI runner with the repo's own venv, from wherever the repo lives.
ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "Running NLP Playwright Test..."
"$ROOT/.venv/bin/python" "$ROOT/runner.py" "$@"
