"""
Run every script-style check in tests/ (the ones pytest skips) and summarise.

    python tests/run_all.py            # all
    python tests/run_all.py rename     # only files whose name contains "rename"

Each file runs in its own process (they call sys.exit and some start a
browser). Exit code is non-zero when any file fails.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from conftest import collect_ignore  # noqa: E402


#: Several script checks write through the real API into data/ (elements, step
#: groups, Test Data). They are restored afterwards so a test run never changes
#: what the team is working with.
_PROTECT = ["data/locators_manual.json", "data/reusable_steps.json",
            "data/common/variables.json", "data/recorded_elements.json"]


def _snapshot() -> dict:
    out = {}
    for rel in _PROTECT:
        path = os.path.join(ROOT, rel)
        if os.path.exists(path):
            with open(path, "rb") as f:
                out[path] = f.read()
    return out


def _restore(snap: dict) -> None:
    for path, data in snap.items():
        with open(path, "wb") as f:
            f.write(data)


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(f for f in collect_ignore if only in f)
    snap = _snapshot()
    try:
        return _run(files)
    finally:
        _restore(snap)


def _run(files: list) -> int:
    results = []
    for f in files:
        t = time.time()
        try:
            p = subprocess.run([sys.executable, os.path.join(HERE, f)], cwd=ROOT,
                               capture_output=True, text=True, timeout=600)
            code, tail = p.returncode, (p.stdout + p.stderr).strip().splitlines()[-3:]
        except subprocess.TimeoutExpired:
            code, tail = -1, ["timed out after 600 s"]
        results.append((f, code, time.time() - t, tail))
        print(f"{'PASS' if code == 0 else 'FAIL'}  {f}  ({time.time() - t:.0f}s)")
        if code != 0:
            for line in tail:
                print("      " + line)
    failed = [r for r in results if r[1] != 0]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
