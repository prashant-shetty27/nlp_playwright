"""
Team sync — git for people who never want to see git.

The team shares test cases, elements, step groups, suites and plans through a
git repository, but most testers are not developers: `git pull` and `git push`
are not steps they should have to know. This module gives the portal two plain
actions:

  get_latest()   bring in what the team has shared (git pull --ff-only)
  share(user)    hand my test cases / elements to the team (add, commit, push)

Only the files that are meant to be shared travel (SHARED_PATHS). Per-machine
files — .env, Test Data with credentials, screenshots, reports, run state —
are git-ignored and never move. Every result is reported in plain words; the
git output is kept for the "details" view only.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field

from config.settings import BASE_DIR

#: What "my work" means: everything a tester authors in the portal.
SHARED_PATHS = ["flows", "data/locators_manual.json", "data/reusable_steps.json",
                "data/test_case_folders.json", "suites", "plans"]

_LABEL = {"flows": "test case", "suites": "test suite", "plans": "test plan",
          "data/locators_manual.json": "elements", "data/reusable_steps.json": "step groups",
          "data/test_case_folders.json": "folders"}


@dataclass
class SyncResult:
    ok: bool
    summary: str                 # one plain sentence for the notification
    details: str = ""            # raw git output, for the expandable "details"
    restart_needed: bool = False # code changed → portal must restart to use it
    updated_files: list[str] = field(default_factory=list)


def _git(*args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "git is not installed on this computer."
    except subprocess.TimeoutExpired:
        return 124, "git took too long (network?)."


def is_repo() -> bool:
    return _git("rev-parse", "--is-inside-work-tree")[0] == 0


def status() -> dict:
    """What the Settings page shows: behind/ahead counts and my unshared work."""
    if not is_repo():
        return {"repo": False}
    _git("fetch", "--quiet", timeout=60)
    rc, out = _git("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    ahead = behind = 0
    if rc == 0 and out:
        try:
            ahead, behind = (int(x) for x in out.split())
        except ValueError:
            pass
    rc, out = _git("status", "--porcelain", "--", *SHARED_PATHS)
    changes: dict[str, int] = {}
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        top = path.split("/")[0] if not path.startswith("data/") else path
        label = _LABEL.get(top, top)
        changes[label] = changes.get(label, 0) + 1
    rc, log = _git("log", "--oneline", "HEAD..@{upstream}", "-n", "10")
    incoming = [l.split(" ", 1)[1] if " " in l else l for l in log.splitlines()] if rc == 0 else []
    rc, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    return {"repo": True, "branch": branch if rc == 0 else "", "ahead": ahead, "behind": behind,
            "my_changes": changes, "incoming": incoming}


def _describe(changes: dict[str, int]) -> str:
    parts = []
    for label, n in changes.items():
        if label in ("elements", "step groups", "folders"):
            parts.append(label)
        else:
            parts.append(f"{n} {label}{'s' if n != 1 else ''}")
    return ", ".join(parts) if parts else "nothing"


def get_latest() -> SyncResult:
    """git pull --ff-only, explained. Never merges over a tester's uncommitted work."""
    if not is_repo():
        return SyncResult(False, "This folder is not connected to the team repository.")
    before = _git("rev-parse", "HEAD")[1]
    rc, out = _git("pull", "--ff-only", "--quiet")
    if rc != 0:
        if "unstaged changes" in out or "would be overwritten" in out or "local changes" in out:
            return SyncResult(False, "You have unshared work that the team also changed. Share your "
                                     "work first (button below), then get the latest.", out)
        if "not possible to fast-forward" in out or "diverg" in out:
            return SyncResult(False, "Your copy and the team's copy have drifted apart. Share your "
                                     "work first; if that also fails, send Prashant the details.", out)
        return SyncResult(False, "Could not get the latest — check the network / VPN and try again.", out)
    after = _git("rev-parse", "HEAD")[1]
    if before == after:
        return SyncResult(True, "You already have the latest from the team.", out)
    rc, files = _git("diff", "--name-only", before, after)
    changed = files.splitlines() if rc == 0 else []
    code = [f for f in changed if not any(f == p or f.startswith(p + "/") for p in SHARED_PATHS)]
    n_tests = sum(1 for f in changed if f.startswith("flows/"))
    what = []
    if n_tests:
        what.append(f"{n_tests} test case{'s' if n_tests != 1 else ''}")
    if any(f == "data/locators_manual.json" for f in changed):
        what.append("elements")
    if any(f == "data/reusable_steps.json" for f in changed):
        what.append("step groups")
    if code:
        what.append(f"portal updates ({len(code)} files)")
    summary = "Got the latest: " + (", ".join(what) or f"{len(changed)} files") + "."
    if code:
        summary += " The portal will restart to apply the updates."
    return SyncResult(True, summary, out, restart_needed=bool(code), updated_files=changed)


def share(user: str, message: str = "") -> SyncResult:
    """Add + commit + pull --rebase + push, only the shared paths."""
    if not is_repo():
        return SyncResult(False, "This folder is not connected to the team repository.")
    st = status()
    if not st.get("my_changes"):
        return SyncResult(True, "Nothing to share — all your work is already with the team.")
    what = _describe(st["my_changes"])
    rc, out = _git("add", "-A", "--", *SHARED_PATHS)
    if rc != 0:
        return SyncResult(False, "Could not prepare your changes.", out)
    # The same guard as the pre-commit hook: no secrets, no literal test
    # numbers, no per-machine files — explained in plain words.
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(BASE_DIR, "tools"))
        from check_commit import staged as _staged, check_files as _check
        names, contents = _staged()
        problems = _check(names, contents)
    except Exception:  # noqa: BLE001
        problems = []
    if problems:
        _git("reset", "-q")
        return SyncResult(False, "Not shared — something private is inside your changes. "
                                 "Fix these lines and try again:\n" + "\n".join(problems[:8]),
                          "\n".join(problems))
    who = (user or "portal").strip() or "portal"
    msg = (message or "").strip() or f"{who}: {what}"
    rc, out = _git("-c", f"user.name={who}", "-c", f"user.email={who}@portal.local",
                   "commit", "-q", "-m", msg)
    if rc != 0 and "nothing to commit" not in out:
        return SyncResult(False, "Could not save your changes.", out)
    rc, out2 = _git("pull", "--rebase", "--quiet")
    if rc != 0:
        _git("rebase", "--abort")
        return SyncResult(False, "Someone changed the same file as you. Your work is saved on this "
                                 "computer; send Prashant the details so it can be merged.", out2)
    rc, out3 = _git("push", "--quiet")
    if rc != 0:
        if "Authentication" in out3 or "could not read Username" in out3 or "403" in out3:
            return SyncResult(False, "GitHub did not accept your login. Your work is saved on this "
                                     "computer; sign in to GitHub once from a terminal (git push) "
                                     "and try again.", out3)
        return SyncResult(False, "Could not send your work — check the network / VPN and try again. "
                                 "Nothing is lost; it is saved on this computer.", out3)
    return SyncResult(True, f"Shared with the team: {what}.", "\n".join(x for x in (out, out2, out3) if x))
