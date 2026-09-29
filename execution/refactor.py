"""
execution/refactor.py — rename a thing, and fix everything that pointed at it.

Renaming was not possible at all: a test case, an element or a test-data value
got its name at creation and kept it. The workaround — delete and re-create —
is worse than it sounds, because names are referenced by name in three places
that nothing would have updated:

    a flow name       → suites/*.json and plans/*.json list flows by name
    an element name   → written into the steps of every flow that clicks it
    a variable name   → written as ${name} into steps, and into "# Params" headers

So a rename that only touched the thing itself would leave a suite pointing at a
flow that no longer exists, or a step clicking an element that was renamed out
from under it. Both fail at run time, long after the rename, and look like
unrelated breakage.

Every function here is preview-first: called without apply=True it reports
exactly what WOULD change and touches nothing. The caller shows that list, and
only then applies it. A rename with a blast radius is a decision, not a
keystroke.
"""
from __future__ import annotations

import json
import os
import re

from config import settings

BASE_DIR = settings.BASE_DIR
FLOWS_DIR = os.path.join(BASE_DIR, "flows")
SUITES_DIR = os.path.join(BASE_DIR, "suites")
PLANS_DIR = os.path.join(BASE_DIR, "plans")


class RenameError(ValueError):
    """A rename that must not proceed, with a reason fit to show a person."""


def _iter_files(root: str, suffix: str):
    if not os.path.isdir(root):
        return
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(suffix):
                yield os.path.join(dirpath, f)


def _rel(path: str) -> str:
    return os.path.relpath(path, BASE_DIR)


def _word_re(name: str) -> re.Pattern:
    """Match `name` only as a whole word, so renaming `search` spares `search_box`."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")


# ── finding references ────────────────────────────────────────────────────────

def references_to_flow(name: str) -> list[dict]:
    """Suites and plans that name this flow."""
    hits: list[dict] = []
    pattern = _word_re(name)
    for root in (SUITES_DIR, PLANS_DIR):
        for path in _iter_files(root, ".json"):
            try:
                text = open(path, "r", encoding="utf-8").read()
            except OSError:
                continue
            if pattern.search(text):
                hits.append({"file": _rel(path), "kind": "suite/plan",
                             "occurrences": len(pattern.findall(text))})
    return hits


def _references_in_flows(pattern: re.Pattern) -> list[dict]:
    hits: list[dict] = []
    for path in _iter_files(FLOWS_DIR, ".flow"):
        try:
            lines = open(path, "r", encoding="utf-8").read().splitlines()
        except OSError:
            continue
        found = [{"line": i, "text": ln.strip()}
                 for i, ln in enumerate(lines, 1) if pattern.search(ln)]
        if found:
            hits.append({"file": _rel(path), "kind": "flow",
                         "occurrences": len(found), "lines": found[:8]})
    return hits


def references_to_locator(name: str) -> list[dict]:
    return _references_in_flows(_word_re(name)) + _references_in_step_groups(_word_re(name))


def _references_in_step_groups(pattern: re.Pattern) -> list[dict]:
    """
    Step groups that name this element.

    A group is expanded inline wherever it is called, so an element renamed in
    the flows but not in the groups breaks every test that calls one — and the
    failure points at the group, not at the rename that caused it.
    """
    try:
        from core.reusable_steps import describe
    except Exception:  # noqa: BLE001
        return []
    hits = []
    for g in describe():
        found = [{"line": i, "text": st}
                 for i, st in enumerate(g.get("steps", []), 1) if pattern.search(st)]
        if found:
            hits.append({"file": f"step group: {g['name']}", "kind": "step group",
                         "group": g["name"], "occurrences": len(found),
                         "lines": found[:8]})
    return hits


def references_to_variable(name: str) -> list[dict]:
    """${name} in a step, and the name alone on a "# Params" header line."""
    return _references_in_flows(
        re.compile(rf"(\$\{{{re.escape(name)}\}})|(^#\s*Params\s*:.*(?<![A-Za-z0-9_])"
                   rf"{re.escape(name)}(?![A-Za-z0-9_]))", re.M))


# ── renaming ──────────────────────────────────────────────────────────────────

def rename_flow(old: str, new: str, *, apply: bool = False) -> dict:
    """
    Rename a test case, its sidecar map, and every suite/plan that lists it.
    """
    from api.routes.projects import _flow_path

    try:
        src, dst = _flow_path(old), _flow_path(new)
    except ValueError as e:
        raise RenameError(str(e)) from e
    if not os.path.exists(src):
        raise RenameError(f"No test case named {old!r}.")
    if os.path.exists(dst) and os.path.realpath(dst) != os.path.realpath(src):
        raise RenameError(f"A test case named {os.path.basename(dst)[:-5]!r} already exists.")

    new_name = os.path.basename(dst)[:-len(".flow")]
    changes = [{"file": _rel(src), "kind": "rename",
                "detail": f"{os.path.basename(src)} → {os.path.basename(dst)}"}]

    sidecar = os.path.splitext(src)[0] + ".map.json"
    if os.path.exists(sidecar):
        changes.append({"file": _rel(sidecar), "kind": "rename",
                        "detail": f"{os.path.basename(sidecar)} → {new_name}.map.json"})

    refs = references_to_flow(old)
    changes.extend({**r, "kind": "update reference"} for r in refs)

    if not apply:
        return {"applied": False, "new_name": new_name, "changes": changes}

    os.rename(src, dst)
    if os.path.exists(sidecar):
        os.rename(sidecar, os.path.join(os.path.dirname(dst), new_name + ".map.json"))

    for ref in refs:
        path = os.path.join(BASE_DIR, ref["file"])
        try:
            _rewrite_flow_refs(path, old, new_name)
        except (OSError, ValueError):
            continue
    return {"applied": True, "new_name": new_name, "changes": changes}


def _rewrite_flow_refs(path: str, old: str, new: str) -> None:
    """
    Rename the flow inside one suite/plan JSON — structurally.

    A word-regex over the raw text also renamed JSON KEYS: a test case called
    `status`, `enabled`, `time` or `date` (all valid names) rewrote the plan's
    own fields and silently disabled its schedule. Only string VALUES that
    name the flow change: "flows/<old>.flow", "<old>.flow" or exactly "<old>".
    """
    import json

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    forms = {f"flows/{old}.flow": f"flows/{new}.flow", f"{old}.flow": f"{new}.flow", old: new}

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        if isinstance(o, str):
            return forms.get(o, o)
        return o

    with open(path, "w", encoding="utf-8") as f:
        json.dump(walk(data), f, indent=2, ensure_ascii=False)
        f.write("\n")


def rename_locator(page: str, old: str, new: str, *, apply: bool = False) -> dict:
    """
    Rename an element and rewrite every step that referred to it.

    Only the writable database can be renamed. A recorded element belongs to the
    spy; renaming it here would leave the recording and the database disagreeing.
    """
    from locators.manager import load_locators, normalise_name, save_locators
    from locators.sources import owner, sources

    try:
        new_name = normalise_name(new, kind="locator")
    except ValueError as e:
        raise RenameError(str(e)) from e

    existing = owner(old, "website")
    if existing is not None:
        src = next((s for s in sources() if s.id == existing.source_id), None)
        if src is not None and not src.writable:
            raise RenameError(
                f"'{old}' lives in {src.label}, which is not editable here. "
                f"Re-record it under the new name, or add an overriding entry.")

    data = load_locators()
    if page not in data or old not in data[page]:
        raise RenameError(f"No element {page}/{old} in the editable database.")
    if new_name in data[page] and new_name != old:
        raise RenameError(f"{page}/{new_name} already exists.")
    clash = owner(new_name, "website")
    if clash is not None and clash.name != old:
        raise RenameError(
            f"'{new_name}' is already defined in {clash.source_id}:{clash.group} "
            f"and would be resolved there instead.")

    refs = references_to_locator(old)
    changes = [{"file": "data/locators_manual.json", "kind": "rename",
                "detail": f"{page}/{old} → {page}/{new_name}"}]
    changes.extend({**r, "kind": "update steps"} for r in refs)

    if not apply:
        return {"applied": False, "new_name": new_name, "changes": changes}

    data[page][new_name] = data[page].pop(old)
    save_locators(data)
    pattern = _word_re(old)
    _rewrite_flows(pattern, new_name, [r for r in refs if r.get("kind") != "step group"])
    _rewrite_step_groups(pattern, new_name,
                         [r for r in refs if r.get("kind") == "step group"])
    return {"applied": True, "new_name": new_name, "changes": changes}


def merge_locators(keep: str, drop: list[str], *, apply: bool = False) -> dict:
    """
    Fold duplicate elements into one: every step that names any of `drop`
    is rewritten to `keep`, then the dropped elements are deleted from the
    editable database. Recorded (spy) copies cannot be deleted here — they
    are reported and left; with manual-first precedence they no longer win.
    """
    from locators.manager import load_locators, save_locators
    from locators.sources import owner

    if owner(keep, "website") is None:
        raise RenameError(f"'{keep}' does not exist, so nothing can be merged into it.")
    drop = [d for d in dict.fromkeys(drop) if d and d != keep]
    if not drop:
        raise RenameError("Nothing to merge.")
    data = load_locators()
    changes: list[dict] = []
    left: list[str] = []
    plan: list[tuple[str, str, list[dict]]] = []   # (name, group, refs)
    for name in drop:
        grp = next((g for g, els in data.items() if isinstance(els, dict) and name in els), "")
        refs = references_to_locator(name)
        changes.extend({**r, "kind": f"rewrite {name} → {keep}"} for r in refs)
        if grp:
            changes.append({"file": "data/locators_manual.json", "kind": "delete",
                            "detail": f"{grp}/{name}"})
        else:
            left.append(name)
        plan.append((name, grp, refs))
    if not apply:
        return {"applied": False, "keep": keep, "changes": changes, "not_deletable": left}
    for name, grp, refs in plan:
        pattern = _word_re(name)
        _rewrite_flows(pattern, keep, [r for r in refs if r.get("kind") != "step group"])
        _rewrite_step_groups(pattern, keep, [r for r in refs if r.get("kind") == "step group"])
        if grp:
            data = load_locators()
            data.get(grp, {}).pop(name, None)
            if grp in data and not data[grp]:
                del data[grp]
            save_locators(data)
    return {"applied": True, "keep": keep, "changes": changes, "not_deletable": left}


def _rewrite_step_groups(pattern: re.Pattern, replacement: str,
                         refs: list[dict]) -> None:
    """Apply a rename inside every step group that referenced the old name."""
    if not refs:
        return
    try:
        from core.reusable_steps import describe, get, save
    except Exception:  # noqa: BLE001
        return
    platforms = {g["name"]: g.get("platform", "") for g in describe()}
    for ref in refs:
        name = ref.get("group")
        if not name:
            continue
        try:
            steps = [pattern.sub(replacement, st) for st in get(name)]
            save(name, steps, overwrite=True, platform=platforms.get(name, ""))
        except Exception:  # noqa: BLE001 — one bad group must not abort the rest
            continue


def rename_variable(old: str, new: str, *, scope: str = "global",
                    environment: str = "", apply: bool = False) -> dict:
    """Rename a test-data value and every ${reference} and header naming it."""
    from execution import test_data
    from nlp.variables import normalise

    new_name = normalise(new)
    if not new_name:
        raise RenameError(f"{new!r} is not a usable variable name.")

    current = test_data.get_all(environment)
    if old not in current:
        raise RenameError(f"No value named {old!r}.")
    if new_name in current and new_name != old:
        raise RenameError(f"A value named {new_name!r} already exists.")

    refs = references_to_variable(old)
    changes = [{"file": "data/common/variables.json", "kind": "rename",
                "detail": f"{old} → {new_name}"}]
    changes.extend({**r, "kind": "update steps"} for r in refs)

    if not apply:
        return {"applied": False, "new_name": new_name, "changes": changes}

    entry = current[old]
    test_data.set_value(new_name, entry.get("value", ""),
                        scope=entry.get("scope", scope),
                        environment=entry.get("environment", environment))
    test_data.delete_value(old, scope=entry.get("scope", scope),
                           environment=entry.get("environment", environment))

    # ${old} in steps, and the bare name on a "# Params" header.
    _rewrite_flows(re.compile(r"\$\{" + re.escape(old) + r"\}"), "${" + new_name + "}", refs)
    _rewrite_flows(re.compile(rf"^(#\s*Params\s*:.*?)(?<![A-Za-z0-9_])"
                              rf"{re.escape(old)}(?![A-Za-z0-9_])", re.M),
                   rf"\g<1>{new_name}", refs)
    return {"applied": True, "new_name": new_name, "changes": changes}


def _rewrite_flows(pattern: re.Pattern, replacement: str, refs: list[dict]) -> None:
    for ref in refs:
        path = os.path.join(BASE_DIR, ref["file"])
        try:
            text = open(path, "r", encoding="utf-8").read()
            open(path, "w", encoding="utf-8").write(pattern.sub(replacement, text))
        except OSError:
            continue
