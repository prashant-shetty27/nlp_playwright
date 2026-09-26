"""
core/run_types.py — Smoke / Sanity / Regression / Full: which steps a run executes.

Tagging (backward compatible — the runner treats every '#' line as a comment,
so untagged flows and every existing run behave exactly as before):

    # Tags: regression                      flow header: default for the flow
    # --- Purpose: TC_RP_HEADING_PRODUCT [smoke, sanity] — heading is shown
                                            band tags, in [ ] on the purpose line

A band without tags inherits the flow's tags; a flow without '# Tags:' is
"regression". Steps before the first band are SETUP and run whenever anything
in the flow runs. '# OFF:' lines never run in any type (lead submission etc.).

Which bands each type runs (the classic testing pyramid — each level is a
superset of the one above, so a green Smoke is a precondition, not a
different test):

    smoke       [smoke]                         "is the build alive?"  — minutes
    sanity      [smoke, sanity]                 "does the changed area work?"
    regression  [smoke, sanity, regression]     "did anything else break?"
    full        everything, incl. [full]        slow / exhaustive / rare cases
"""
from __future__ import annotations

import re

PURPOSE = "# --- Purpose:"
RUN_TYPES = ("smoke", "sanity", "regression", "full")
TAGS = ("smoke", "sanity", "regression", "full")
INCLUDES = {
    "smoke": {"smoke"},
    "sanity": {"smoke", "sanity"},
    "regression": {"smoke", "sanity", "regression"},
    "full": {"smoke", "sanity", "regression", "full"},
}
#: Execution defaults that make each type do its job (overridable per plan).
DEFAULTS = {
    "smoke": {"stop_on_failure": True, "stop_on_first_failure": True, "retry_failed": False,
              "screenshot_mode": "failure", "report": "quick"},
    "sanity": {"stop_on_failure": False, "stop_on_first_failure": False, "retry_failed": True,
               "screenshot_mode": "key", "report": "quick"},
    "regression": {"stop_on_failure": False, "stop_on_first_failure": False, "retry_failed": True,
                   "screenshot_mode": "all", "report": "detailed"},
    "full": {"stop_on_failure": False, "stop_on_first_failure": False, "retry_failed": True,
             "screenshot_mode": "all", "report": "detailed"},
}
LABEL = {"smoke": "Smoke", "sanity": "Sanity", "regression": "Regression", "full": "Full"}
ICON = {"smoke": "🔥", "sanity": "🎯", "regression": "🔁", "full": "🧪"}

_BAND_TAGS = re.compile(r"\[([a-zA-Z ,_-]+)\]")
_HEADER_TAGS = re.compile(r"^#\s*Tags\s*:\s*(.+)$", re.I)
_STORES = re.compile(r"\bas\s+([A-Za-z_][\w]*)\s*$|\bstore\b.*?\bas\s+([A-Za-z_][\w]*)", re.I)
_USES = re.compile(r"\$\{([A-Za-z_][\w]*)\}")


def _stored(line: str) -> str:
    """Variable a step stores ('... as name'), or ''."""
    m = _STORES.search(line.strip())
    return (m.group(1) or m.group(2) or "") if m else ""


def normalise(run_type: str | None) -> str:
    t = (run_type or "").strip().lower()
    return t if t in RUN_TYPES else ""


def _parse_tags(text: str) -> set[str]:
    return {t.strip().lower() for t in re.split(r"[,\s]+", text) if t.strip().lower() in TAGS}


def band_tags(purpose_line: str) -> set[str]:
    head = purpose_line.split(" — ")[0]          # tags live before the description
    out: set[str] = set()
    for m in _BAND_TAGS.finditer(head):
        out |= _parse_tags(m.group(1))
    return out


def analyse(lines: list[str]) -> dict:
    """
    Split a flow into setup + bands.
    Returns {"flow_tags", "setup": [line numbers], "bands": [{name, tags, start, lines: [...]}]}
    (line numbers are 1-based, runnable lines only).
    """
    flow_tags: set[str] = set()
    setup: list[int] = []
    bands: list[dict] = []
    cur = None
    for n, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s:
            continue
        if s.startswith(PURPOSE):
            text = s[len(PURPOSE):].strip()
            name = re.split(r"\s[—-]\s|\s\[", text, maxsplit=1)[0].strip() or f"band@{n}"
            cur = {"name": name, "tags": band_tags(s), "start": n, "lines": [], "text": text}
            bands.append(cur)
            continue
        m = _HEADER_TAGS.match(s)
        if m and cur is None:
            flow_tags |= _parse_tags(m.group(1))
            continue
        if s.startswith("#"):
            continue
        (cur["lines"] if cur else setup).append(n)
    base = flow_tags or {"regression"}
    for b in bands:
        b["effective"] = b["tags"] or base
    return {"flow_tags": sorted(base), "setup": setup, "bands": bands}


def select(lines: list[str], run_type: str | None) -> dict:
    """
    Which runnable lines a run of this type executes.
    Returns {"run": set(line numbers) | None (None = everything, no filtering),
             "bands_in": [...], "bands_out": [...], "warnings": [...]}.
    """
    rt = normalise(run_type)
    if not rt or rt == "full":
        return {"run": None, "bands_in": [], "bands_out": [], "warnings": []}
    want = INCLUDES[rt]
    a = analyse(lines)
    run: set[int] = set()
    bands_in, bands_out = [], []
    if not a["bands"]:
        if want & set(a["flow_tags"]):
            return {"run": None, "bands_in": ["(whole test case)"], "bands_out": [], "warnings": []}
        return {"run": set(), "bands_in": [], "bands_out": ["(whole test case)"], "warnings": []}
    for b in a["bands"]:
        if want & b["effective"]:
            run.update(b["lines"])
            bands_in.append(b["name"])
        else:
            bands_out.append(b["name"])
    if run:
        run.update(a["setup"])
    # A kept band that reads ${x} which only a skipped band stores will fail
    # for the wrong reason — say so before the run, not after.
    warnings = []
    stored_by: dict[str, str] = {}
    for b in a["bands"]:
        for n in b["lines"]:
            v = _stored(lines[n - 1])
            if v:
                stored_by.setdefault(v, b["name"])
    for b in a["bands"]:
        if b["name"] not in bands_in:
            continue
        own = {_stored(lines[n - 1]) for n in b["lines"]} - {""}
        for n in b["lines"]:
            for v in _USES.findall(lines[n - 1]):
                src = stored_by.get(v)
                if src and src not in bands_in and v not in own:
                    warnings.append(f"{b['name']} uses ${{{v}}}, which is only stored in {src} "
                                    f"(not part of this {LABEL[rt]} run)")
    return {"run": run, "bands_in": bands_in, "bands_out": bands_out,
            "warnings": sorted(set(warnings))}


def preview(flow_path: str, run_type: str | None) -> dict:
    """Step counts for a flow under a run type — for the plan editor."""
    with open(flow_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    total = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#"))
    sel = select(lines, run_type)
    n = total if sel["run"] is None else len(sel["run"])
    return {"steps": n, "total": total, "bands_in": sel["bands_in"],
            "bands_out": sel["bands_out"], "warnings": sel["warnings"]}
