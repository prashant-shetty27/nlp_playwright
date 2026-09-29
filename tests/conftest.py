"""
pytest configuration.

Most files in tests/ are SCRIPT-style checks: they run at import time and end
with a module-level sys.exit(), which crashed `python -m pytest` during
collection. pytest now skips those files; run them with `python tests/run_all.py`.
Real pytest tests live in tests/unit/ (and any test_*.py without a top-level
sys.exit).
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = re.compile(r"^sys\.exit\(", re.M)


def _script_style(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return bool(_SCRIPT.search(f.read()))
    except OSError:
        return False


collect_ignore = [n for n in os.listdir(_HERE)
                  if n.startswith("test_") and n.endswith(".py")
                  and _script_style(os.path.join(_HERE, n))]
# Script checks with no sys.exit() that still launch real browsers / hit the
# network at import time — collecting them under plain `pytest` opened
# Chromium against justdial.com and contributed no test.
collect_ignore += ["test_platforms.py", "test_mobile_web.py"]
