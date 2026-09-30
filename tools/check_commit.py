#!/usr/bin/env python3
"""
Pre-commit guard: nothing dynamic, private or machine-specific reaches git.

Runs on the STAGED files (git pre-commit hook) and from Team sync before a
share. Refuses the commit and says exactly which line is the problem.

What it refuses
  * credentials: user:pass@host in URLs, API keys / tokens / private keys,
    "password": "…" in JSON, AUTH_*_PASSWORD= values
  * the blocked test mobile numbers typed literally (use ${mobile_9987} …)
  * files that are per machine or per run and must stay out of git:
    .env, data/users.json, data/common/variables.json, config/environments.json,
    data/plan_state.json, runtime_variables.json, *.lock, screenshots, logs,
    reports, run records, testsigma exports, videos
  * plan files carrying run state (next_run / last_run) — that state lives in
    data/plan_state.json

Install once per clone:  python tools/check_commit.py --install
Check the staged files:  python tools/check_commit.py
Check the whole repo:    python tools/check_commit.py --all
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NEVER_COMMIT = [
    r"^\.env$", r"^\.env\..*(?<!example)$", r"^data/users\.json$", r"^data/common/variables\.json$",
    r"^config/environments\.json$", r"^data/plan_state\.json$", r"runtime_variables\.json$",
    r"\.lock$", r"^data/screenshots/", r"^screenshots/", r"^data/uploads/", r"\.zip$", r"^data/videos/", r"^data/logs/", r"^data/plan_runs/",
    r"^data/plan_reports/", r"^data/testsigma_exports/", r"^data/meta/", r"^data/\.session_secret$",
    r"^data/test_users\.(csv|xlsx)$", r"\.bak$", r"\.bak_\d+$",
]
SECRET_PATTERNS = [
    (re.compile(r"https?://[^/\s:'\"]+:[^/\s@'\"]+@[A-Za-z0-9.-]+"), "a login inside a URL (user:password@host)"),
    (re.compile(r"\b(sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|ghp_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,})"), "an API key / token"),
    (re.compile(r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\"(password|passwd|pin|api_key|apikey|token|secret)\"\s*:\s*\"[^\"]{3,}\"", re.I), "a password / token value in JSON"),
    (re.compile(r"^\s*(AUTH_[A-Z0-9_]+_PASSWORD|OTP_PORTAL_PIN|SLACK_BOT_TOKEN|SLACK_WEBHOOK_URL|JIRA_PAT|TESTSIGMA_API_KEY|ANTHROPIC_API_KEY|OPENAI_API_KEY|API_TOKEN|UI_SESSION_SECRET|EMAIL_PASSWORD)\s*=\s*[^\n]+", re.M), "a secret setting with its value"),
]
TEXT_EXT = (".flow", ".json", ".py", ".md", ".txt", ".yml", ".yaml", ".ps1", ".sh", ".cmd", ".example", ".xml", ".cfg", ".ini", ".toml")


_PLACEHOLDER = re.compile(r"<[^>]*>|…|\.\.\.|example\.|your[-_ ]|xxx|user:pass@|username:password@|reqres\.in|cityslicka|personal access token", re.I)


def _placeholder(match: str) -> bool:
    """A documented shape, not a real value."""
    return bool(_PLACEHOLDER.search(match))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout


def test_mobiles() -> set[str]:
    nums = set()
    for src in (os.environ.get("TEST_MOBILES", ""),):
        nums |= {n.strip() for n in src.split(",") if n.strip()}
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8", errors="replace"):
            if line.startswith("TEST_MOBILES="):
                nums |= {n.strip() for n in line.split("=", 1)[1].split(",") if n.strip()}
    return {n for n in nums if re.fullmatch(r"\d{10}", n)}


def check_files(paths: list[str], contents=None) -> list[str]:
    problems: list[str] = []
    mobiles = test_mobiles()
    for path in paths:
        p = path.replace(os.sep, "/")
        for pat in NEVER_COMMIT:
            if re.search(pat, p):
                problems.append(f"{p}: this file is per machine / per run and must not be committed")
                break
        else:
            if not p.lower().endswith(TEXT_EXT):
                continue
            text = contents[p] if contents and p in contents else None
            if text is None:
                try:
                    with open(os.path.join(ROOT, p), encoding="utf-8", errors="replace") as f:
                        text = f.read()
                except OSError:
                    continue
            if p.endswith(".example") or p.endswith("check_commit.py"):
                continue
            for rx, what in SECRET_PATTERNS:
                for m in rx.finditer(text):
                    if _placeholder(m.group(0)):
                        continue          # documentation: <token>, example.com, user:pass@host
                    line = text.count("\n", 0, m.start()) + 1
                    problems.append(f"{p}:{line}: contains {what}")
                    break
            for n in mobiles:
                if re.search(r"[\"'\s]" + n + r"[\"'\s]", text):
                    line = text.count("\n", 0, text.index(n)) + 1
                    problems.append(f"{p}:{line}: test mobile number typed literally — use ${{mobile_{n[:4]}}} from Test Data")
            if p.endswith((".flow", "reusable_steps.json")):
                m = re.search(r'(?:type|enter\s+otp)[^"\n]*"(\d{4,6})"[^\n]*otp', text, re.I)
                if m:
                    line = text.count("\n", 0, m.start()) + 1
                    problems.append(f"{p}:{line}: OTP typed literally — use ${{web_otp}} (Website) or ${{touch_otp}} (Mobile Site) from Test Data")
            if p.startswith("plans/") and p.endswith(".json") and re.search(r"\"(next_run|last_run)\"", text):
                problems.append(f"{p}: carries run state (next_run/last_run) — it belongs in data/plan_state.json")
    return problems


def staged() -> tuple[list[str], dict]:
    names = [l for l in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if l]
    contents = {}
    for n in names:
        contents[n] = subprocess.run(["git", "show", f":{n}"], cwd=ROOT, capture_output=True,
                                     text=True, errors="replace").stdout
    return names, contents


def install_hook() -> None:
    hook = os.path.join(ROOT, ".git", "hooks", "pre-commit")
    os.makedirs(os.path.dirname(hook), exist_ok=True)
    with open(hook, "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n# Installed by tools/check_commit.py --install\n"
                "ROOT=\"$(git rev-parse --show-toplevel)\"\n"
                "for PY in \"$ROOT/.venv/bin/python\" \"$ROOT/.venv/Scripts/python.exe\" python3 python; do\n"
                "  if command -v \"$PY\" >/dev/null 2>&1 || [ -x \"$PY\" ]; then exec \"$PY\" \"$ROOT/tools/check_commit.py\"; fi\n"
                "done\n"
                "echo 'check_commit: no python found, skipping'; exit 0\n")
    os.chmod(hook, 0o755)
    print(f"pre-commit hook installed at {hook}")


def main(argv: list[str]) -> int:
    if "--install" in argv:
        install_hook()
        return 0
    if "--all" in argv:
        names = [l for l in _git("ls-files").splitlines() if l]
        problems = check_files(names)
    else:
        names, contents = staged()
        problems = check_files(names, contents)
    if problems:
        print("Commit refused — these would put private or per-machine data into git:")
        for p in problems:
            print("  -", p)
        print("Fix the lines above (or 'git reset <file>' to leave the file out) and commit again.")
        return 1
    print(f"check_commit: {len(names)} file(s) clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
