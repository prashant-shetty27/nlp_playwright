"""
core/step_tags.py — per-step run-type tags (Smoke / Sanity / Regression / Full)
with dependency-safe selection.

Every runnable step of a test case has a LEVEL — the lightest run type that
includes it:

    S   smoke        runs in Smoke, Sanity, Regression, Full
    Sy  sanity       runs in Sanity, Regression, Full
    R   regression   runs in Regression, Full
    F   full         runs in Full only

Membership is upward-closed (a Smoke step is always in Sanity too), which is
the testing pyramid the plan runner already uses (core/run_types.py).

Why levels are not just labels
------------------------------
A step cannot run on its own. "verify ${bot_reply} matches …" needs the step
that STORED bot_reply, the page it reads must have been OPENED, the clicks and
typing that put the page in that state must have happened, a login earlier in
the test must still have happened, and a step inside "if … end if" needs its
"if" and "end if". `closure()` computes exactly that set, following
step groups (a `call` line is analysed through the group's own steps, so a
group that stores ${bot_reply} or opens a page counts as doing so).

The invariant kept everywhere (build, every toggle, and again at run time):

    for each run type T:  closure(steps in T) ⊆ steps in T

so a Smoke run can never start half-way through a page, read an unset
variable, or run an "else" without its "if".

Where levels come from
----------------------
* Built automatically whenever a test case is created or saved (and lazily if
  a file was written outside the portal): the first check of the test case is
  Smoke, the first check of every section (a section starts at each page
  `open` / navigation and at each `# --- Purpose:` band) is Sanity, everything
  else is Regression; each seed is expanded with closure(). Author band tags
  ([smoke] / [sanity] / [full] on a purpose line, '# Tags:' header) win over
  the automatic choice.
* Changed by one click in the editor (`toggle`): turning a tag ON for a step
  also turns it on for everything that step needs; turning it OFF also turns it
  off for every step that needs this one. Clicked steps are remembered as
  manual and survive rebuilds.

Storage: flows/.tags/<test case>.json — one entry per runnable line, in file
order, with the step text so edits made outside the portal are re-aligned
(difflib) instead of shifting every tag by one line.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field

LEVELS = ("S", "Sy", "R", "F")
LEVEL_NAME = {"S": "Smoke", "Sy": "Sanity", "R": "Regression", "F": "Full"}
RT_LEVEL = {"smoke": "S", "sanity": "Sy", "regression": "R", "full": "F"}
_IDX = {lv: i for i, lv in enumerate(LEVELS)}

PURPOSE = "# --- Purpose:"
OFF = "# OFF:"

_VAR_USE = re.compile(r"\$\{([A-Za-z_][\w]*)\}|\bstored\s+([A-Za-z_][\w]*)", re.I)
_VAR_STORE = re.compile(r"\bas\s+([A-Za-z_][\w]*)\s*$", re.I)
_LITERAL_STORE = re.compile(r'^store\s+"[^"]*"\s+as\s+[A-Za-z_]\w*\s*$', re.I)
_CALL = re.compile(r"^call\s+(.+?)\s*$", re.I)
# A step that puts the browser / app on a new page: everything after it reads
# that page, nothing before it matters to the page (only to variables/session).
_ANCHOR = re.compile(r"^(open|navigate\s+to|go\s+to|visit|launch\s+(the\s+)?app|"
                     r"restart\s+(the\s+)?app|open\s+app)\b", re.I)
# Read-only checks and evidence: never needed by a later step.
_ASSERT = re.compile(r"^(\[ignore[^\]]*\]\s*)?(verify|assert|check\s+that|expect|"
                     r"take\s+screenshot|capture\s+screenshot|save\s+page\s+source|"
                     r"list\s+tabs|log\b|print\b|compare\b)", re.I)
# State that survives page changes (cookies, chosen city, signed-in user): a
# later step on any page may rely on it.
_SESSION = re.compile(r"(\blog\s*in\b|\blogin\b|\bsign\s*in\b|\botp\b|\$\{otp|\blogout\b|"
                      r"\bsign\s*out\b|delete\s+(all\s+)?cookies?|set\s+cookie|"
                      r"select\s+city|change\s+city|\bset\s+location\b)", re.I)


# ─────────────────────────────────────────────────────────────────────────────
# Step groups
# ─────────────────────────────────────────────────────────────────────────────
def _load_groups(base_dir: str | None = None) -> dict[str, list[str]]:
    base = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "data", "reusable_steps.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f) or {}
    except (OSError, ValueError):
        return {}
    out: dict[str, list[str]] = {}
    items = raw.items() if isinstance(raw, dict) else ((g.get("name"), g) for g in raw)
    for name, g in items:
        if not name:
            continue
        steps = g.get("steps") if isinstance(g, dict) else g
        out[str(name)] = [str(s) for s in (steps or [])]
    return out


def _find_group(name: str, groups: dict[str, list[str]]) -> list[str] | None:
    if name in groups:
        return groups[name]
    low = name.strip().lower()
    for k, v in groups.items():
        if k.strip().lower() == low:
            return v
    return None


@dataclass
class StepInfo:
    line: int                     # 1-based line in the file
    text: str
    kind: str = "action"          # anchor | assert | action | block
    block: str = ""               # control_flow kind for block lines
    uses: set = field(default_factory=set)
    stores: set = field(default_factory=set)
    session: bool = False
    group: str = ""               # step group name for a call line


def _summarise(lines: list[str], groups: dict[str, list[str]], depth: int = 0) -> dict:
    """uses / stores / anchor / session / all-assert for a list of steps (a group body)."""
    uses, stores = set(), set()
    anchor = session = False
    all_assert = True
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = _CALL.match(s)
        if m and depth < 6:
            body = _find_group(m.group(1), groups)
            if body is not None:
                sub = _summarise(body, groups, depth + 1)
                uses |= (sub["uses"] - stores)
                stores |= sub["stores"]
                anchor |= sub["anchor"]
                session |= sub["session"]
                all_assert &= sub["all_assert"]
                continue
        for a, b in _VAR_USE.findall(s):
            v = a or b
            if v not in stores:
                uses.add(v)
        m2 = _VAR_STORE.search(s) if s.lower().startswith(("store", "save", "set", "extract",
                                                           "read", "get", "remember", "copy")) else None
        if m2:
            stores.add(m2.group(1))
        anchor |= bool(_ANCHOR.match(s))
        session |= bool(_SESSION.search(s))
        if not _ASSERT.match(s):
            all_assert = False
    return {"uses": uses, "stores": stores, "anchor": anchor, "session": session,
            "all_assert": all_assert}


# ─────────────────────────────────────────────────────────────────────────────
# Analysis
# ─────────────────────────────────────────────────────────────────────────────
class FlowGraph:
    """Runnable steps of one flow, with what each needs."""

    def __init__(self, lines: list[str], groups: dict[str, list[str]] | None = None):
        from execution.control_flow import classify
        self.lines = lines
        self.groups = groups if groups is not None else _load_groups()
        self.steps: list[StepInfo] = []
        self.bands: list[dict] = []          # {start_idx, tags, name}
        self.header_tags: set[str] = set()
        band_tags_now: set[str] = set()
        self.para_starts: set[int] = {0}     # step indexes that begin a paragraph
        gap = False
        for n, raw in enumerate(lines, 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                gap = gap or bool(self.steps)
            if not s:
                continue
            if s.startswith(PURPOSE):
                from core.run_types import band_tags
                text = s[len(PURPOSE):].strip()
                name = re.split(r"\s[—-]\s|\s\[", text, maxsplit=1)[0].strip() or f"band@{n}"
                band_tags_now = band_tags(s)
                self.bands.append({"start": len(self.steps), "tags": band_tags_now, "name": name})
                continue
            m = re.match(r"^#\s*Tags\s*:\s*(.+)$", s, re.I)
            if m and not self.bands:
                from core.run_types import _parse_tags
                self.header_tags |= _parse_tags(m.group(1))
                continue
            if s.startswith("#"):
                continue
            st = StepInfo(line=n, text=s)
            if gap:
                self.para_starts.add(len(self.steps))
                gap = False
            blk = classify(s)
            if blk:
                st.kind, st.block = "block", blk[0]
                for a, b in _VAR_USE.findall(s):
                    st.uses.add(a or b)
            else:
                mc = _CALL.match(s)
                body = _find_group(mc.group(1), self.groups) if mc else None
                if body is not None:
                    sm = _summarise(body, self.groups)
                    st.group = mc.group(1)
                    st.uses, st.stores, st.session = sm["uses"], sm["stores"], sm["session"]
                    st.kind = "anchor" if sm["anchor"] else ("assert" if sm["all_assert"] else "action")
                else:
                    sm = _summarise([s], self.groups)
                    st.uses, st.stores, st.session = sm["uses"], sm["stores"], sm["session"]
                    st.kind = ("anchor" if sm["anchor"] else
                               "assert" if sm["all_assert"] else "action")
                    # a check that also stores a value is a store
                    if st.kind == "assert" and st.stores:
                        st.kind = "action"
                    # store "<constant>" as x — test data set-up: no side effect,
                    # and the run's input check needs it, so it is always in.
                    if _LITERAL_STORE.match(s) and "${" not in s:
                        st.kind = "setup"
            self.steps.append(st)
        self._cc: dict[int, set[int]] = {}
        self._pair_blocks()

    # blocks ------------------------------------------------------------------
    def _pair_blocks(self) -> None:
        n = len(self.steps)
        self.block_of: list[list[int]] = [[] for _ in range(n)]   # enclosing openers
        self.members: dict[int, list[int]] = {}                   # opener -> its mid/closer lines
        stack: list[int] = []
        for i, st in enumerate(self.steps):
            self.block_of[i] = list(stack)
            k = st.block
            if k in ("if", "for", "times", "until", "while"):
                stack.append(i)
                self.members[i] = []
            elif k in ("elif", "else") and stack:
                self.members[stack[-1]].append(i)
                self.block_of[i] = list(stack[:-1])
            elif k in ("endif", "endloop") and stack:
                o = stack.pop()
                self.members[o].append(i)
                self.block_of[i] = list(stack)
                self.members.setdefault(i, [])
                self._closer_of = getattr(self, "_closer_of", {})
                self._closer_of[i] = o

    # dependencies ------------------------------------------------------------
    def needs(self, i: int) -> set[int]:
        """Steps that step i directly needs to run correctly."""
        st = self.steps[i]
        out: set[int] = set()
        # 1. variables: the latest earlier step that stores each one
        for v in st.uses:
            for j in range(i - 1, -1, -1):
                if v in self.steps[j].stores:
                    out.add(j)
                    break
        # 2. page: the latest page-opening step, and every state-changing step
        #    between it and here (checks in between are not needed)
        anchor = -1
        for j in range(i - 1, -1, -1):
            if self.steps[j].kind == "anchor":
                anchor = j
                break
        start = anchor if anchor >= 0 else 0
        if st.kind not in ("anchor", "setup"):
            for j in range(start, i):
                if self.steps[j].kind not in ("assert", "setup"):
                    out.add(j)
        # 3. session state set earlier anywhere (login, city, cookies)
        if st.kind != "setup":
            for j in range(0, i):
                if self.steps[j].session:
                    out.add(j)
        # 4. blocks: enclosing openers, and an opener's own else/end lines
        for o in self.block_of[i]:
            out.add(o)
            out.update(self.members.get(o, []))
        if i in self.members:
            out.update(self.members[i])
        o = getattr(self, "_closer_of", {}).get(i)
        if o is not None:
            out.add(o)
            out.update(self.members.get(o, []))
        if st.block in ("elif", "else"):
            for o2, mem in self.members.items():
                if i in mem:
                    out.add(o2)
                    out.update(mem)
        out.discard(i)
        return out

    def closure(self, seeds) -> set[int]:
        seeds = list(seeds)
        if len(seeds) == 1:
            c = self._cc.get(seeds[0])
            if c is None:
                c = self._closure(seeds)
                self._cc[seeds[0]] = c
            return set(c)
        out: set[int] = set()
        for x in seeds:
            if x not in out:
                out |= self.closure([x])
        return out

    def _closure(self, seeds) -> set[int]:
        inc: set[int] = set()
        todo = [s for s in seeds if 0 <= s < len(self.steps)]
        while todo:
            i = todo.pop()
            if i in inc:
                continue
            inc.add(i)
            todo.extend(self.needs(i) - inc)
        return inc

    def dependents(self, i: int, within: set[int]) -> set[int]:
        """Steps in `within` whose closure contains i."""
        return {j for j in within if j != i and i in self.closure([j])}

    # automatic levels ----------------------------------------------------------
    def sections(self) -> list[list[int]]:
        cuts = {0} | {b["start"] for b in self.bands} | set(self.para_starts) | \
               {i for i, s in enumerate(self.steps) if s.kind == "anchor"}
        cuts = sorted(c for c in cuts if c < len(self.steps))
        out = []
        for k, c in enumerate(cuts):
            end = cuts[k + 1] if k + 1 < len(cuts) else len(self.steps)
            if end > c:
                out.append(list(range(c, end)))
        return out

    def auto_levels(self) -> list[str]:
        n = len(self.steps)
        if not n:
            return []
        asserts = [i for i, s in enumerate(self.steps) if s.kind == "assert"]
        level = ["R"] * n
        # Author tags decide only when they say something per band, or the whole
        # test case is marked smoke / sanity / full. A plain "# Tags: regression"
        # header still gets automatic Smoke / Sanity picks, remainder Regression.
        tagged = any(b["tags"] for b in self.bands) or \
            bool(self.header_tags & {"smoke", "sanity", "full"})
        rest = "R" if "regression" in self.header_tags else "F"

        def band_steps(b_index: int) -> list[int]:
            b = self.bands[b_index]
            end = self.bands[b_index + 1]["start"] if b_index + 1 < len(self.bands) else n
            return list(range(b["start"], end))

        if tagged:
            base = self.header_tags or {"regression"}
            smoke_seeds, sanity_seeds, full_only = [], [], []
            if not self.bands:
                body = list(range(n))
                if "smoke" in base:
                    smoke_seeds = body
                elif "sanity" in base:
                    sanity_seeds = body
                elif base == {"full"}:
                    full_only = body
            for bi, b in enumerate(self.bands):
                eff = b["tags"] or base
                steps = band_steps(bi)
                if "smoke" in eff:
                    smoke_seeds += steps
                elif "sanity" in eff:
                    sanity_seeds += steps
                elif eff == {"full"}:
                    full_only += steps
            for i in full_only:
                level[i] = "F"
        else:
            # No author tags: Smoke = the first check of the test case; Sanity =
            # the first check of each section (page / paragraph); everything
            # else defaults to Full. Regression is left for the author to pick.
            level = [rest] * n
            secs = [x for x in self.sections() if any(i in asserts for i in x)]
            if not asserts:
                smoke_seeds = sanity_seeds = [n - 1]
            else:
                first = [next(i for i in x if i in asserts) for x in secs]
                smoke_seeds, sanity_seeds = first[:1], first
        if not tagged:
            smoke_seeds = self._with_sibling_branches(smoke_seeds, asserts)
            sanity_seeds = self._with_sibling_branches(sanity_seeds, asserts)
        sy = self.closure(sanity_seeds) | self.closure(smoke_seeds)
        sm = self.closure(smoke_seeds)
        for i in sy:
            level[i] = "Sy"
        for i in sm:
            level[i] = "S"
        for i, st in enumerate(self.steps):      # constant test data: always in
            if st.kind == "setup":
                level[i] = "S"
        return self.normalise(level)

    def _with_sibling_branches(self, seeds, asserts) -> list[int]:
        """A check picked inside an if / else branch only proves something when
        that branch runs — also pick the first check of each sibling branch."""
        out = list(seeds)
        aset = set(asserts)
        for sd in seeds:
            if not self.block_of[sd]:
                continue
            o = self.block_of[sd][-1]
            if self.steps[o].block != "if":
                continue
            bounds = [o] + self.members.get(o, [])
            for a, b in zip(bounds, bounds[1:]):
                if a < sd < b:
                    continue
                first = next((k for k in range(a + 1, b) if k in aset
                              and self.block_of[k] and self.block_of[k][-1] == o), None)
                if first is not None and first not in out:
                    out.append(first)
        return out

    def normalise(self, level: list[str]) -> list[str]:
        """Raise levels until closure(steps in T) ⊆ steps in T for every T."""
        level = list(level)
        changed = True
        while changed:
            changed = False
            for t in ("S", "Sy", "R"):
                members = {i for i, lv in enumerate(level) if _IDX[lv] <= _IDX[t]}
                for i in self.closure(members) - members:
                    level[i] = t
                    changed = True
        return level


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────
def sidecar_path(flow_path: str) -> str:
    d, f = os.path.split(flow_path)
    return os.path.join(d, ".tags", os.path.splitext(f)[0] + ".json")


def _read_lines(flow_path: str) -> list[str]:
    with open(flow_path, "r", encoding="utf-8") as f:
        return f.readlines()


def _load_sidecar(flow_path: str) -> dict | None:
    try:
        with open(sidecar_path(flow_path), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_sidecar(flow_path: str, graph: FlowGraph, level: list[str], manual: list[bool]) -> dict:
    data = {"version": 1, "flow_mtime": os.path.getmtime(flow_path),
            "steps": [{"line": s.line, "text": s.text, "level": level[i], "manual": bool(manual[i])}
                      for i, s in enumerate(graph.steps)]}
    p = sidecar_path(flow_path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)
    return data


def build(flow_path: str, *, groups: dict | None = None, keep_manual: bool = True) -> dict:
    """(Re)build the tags for a flow: automatic levels, carrying manual choices
    over from the previous sidecar by step text (survives inserted/removed lines)."""
    lines = _read_lines(flow_path)
    g = FlowGraph(lines, groups)
    level = g.auto_levels()
    manual = [False] * len(g.steps)
    old = _load_sidecar(flow_path) if keep_manual else None
    if old and old.get("steps"):
        old_texts = [e["text"] for e in old["steps"]]
        new_texts = [s.text for s in g.steps]
        sm = difflib.SequenceMatcher(a=old_texts, b=new_texts, autojunk=False)
        for tag, a0, a1, b0, b1 in sm.get_opcodes():
            if tag != "equal":
                continue
            for k in range(a1 - a0):
                e = old["steps"][a0 + k]
                if e.get("manual") and e.get("level") in _IDX:
                    level[b0 + k] = e["level"]
                    manual[b0 + k] = True
        level = g.normalise(level)
    return _save_sidecar(flow_path, g, level, manual)


def ensure(flow_path: str) -> dict:
    """Sidecar for this flow, rebuilt if missing or older than the flow file."""
    data = _load_sidecar(flow_path)
    try:
        mt = os.path.getmtime(flow_path)
    except OSError:
        return {"steps": []}
    if not data or abs(float(data.get("flow_mtime", 0)) - mt) > 0.001:
        data = build(flow_path)
    return data


def levels_by_line(flow_path: str) -> dict[int, str]:
    return {e["line"]: e["level"] for e in ensure(flow_path).get("steps", [])}


def select_lines(flow_path: str, run_type: str) -> set[int] | None:
    """File lines a run of this type executes (None = every line). Re-checks the
    dependency invariant so a hand-edited sidecar can never break a run."""
    lv = RT_LEVEL.get((run_type or "").lower())
    if not lv or lv == "F":
        return None
    lines = _read_lines(flow_path)
    g = FlowGraph(lines)
    by_line = levels_by_line(flow_path)
    want = {i for i, s in enumerate(g.steps) if _IDX.get(by_line.get(s.line, "R"), 2) <= _IDX[lv]}
    return {g.steps[i].line for i in g.closure(want)}


def toggle(flow_path: str, step_index: int, tag: str) -> dict:
    """
    One click on a tag chip. step_index = 0-based runnable step.
    ON  → the step and everything it needs get at least this level.
    OFF → the step drops below this level, and so does every step that needs it.
    Returns {"steps": [...], "changed": [{"index", "from", "to", "why"}], "message"}.
    """
    if tag not in _IDX:
        raise ValueError(f"unknown tag {tag!r}")
    data = ensure(flow_path)
    lines = _read_lines(flow_path)
    g = FlowGraph(lines)
    level = [e["level"] for e in data["steps"]]
    manual = [bool(e.get("manual")) for e in data["steps"]]
    if len(level) != len(g.steps):
        data = build(flow_path)
        level = [e["level"] for e in data["steps"]]
        manual = [bool(e.get("manual")) for e in data["steps"]]
    if not 0 <= step_index < len(g.steps):
        raise IndexError("no such step")
    before = list(level)
    t = _IDX[tag]
    cur = _IDX[level[step_index]]
    if cur <= t:                                   # currently ON for this tag → OFF
        if tag == "F":
            return {"steps": data["steps"], "changed": [],
                    "message": "Every step runs in a Full run, so Full cannot be switched off."}
        level[step_index] = LEVELS[t + 1]
        manual[step_index] = True
        # cascade down: anything at this tag or lighter that needs the step
        changed = True
        while changed:
            changed = False
            for lv_i in range(0, t + 1):
                members = {i for i, lv in enumerate(level) if _IDX[lv] <= lv_i}
                for j in list(members):
                    if not g.closure([j]) <= members:
                        level[j] = LEVELS[lv_i + 1]
                        manual[j] = True
                        changed = True
        verb = "off"
    else:                                          # OFF → ON
        level[step_index] = tag
        manual[step_index] = True
        for j in g.closure([step_index]):
            if _IDX[level[j]] > t:
                level[j] = tag
        verb = "on"
    level = g.normalise(level)
    saved = _save_sidecar(flow_path, g, level, manual)
    changed = [{"index": i, "line": g.steps[i].line, "from": before[i], "to": level[i]}
               for i in range(len(level)) if level[i] != before[i] and i != step_index]
    name = LEVEL_NAME[tag]
    if verb == "on":
        msg = f"Line {g.steps[step_index].line}: {name} on" + (
            f" — also turned on {len(changed)} step(s) it needs" if changed else "")
    else:
        msg = f"Line {g.steps[step_index].line}: {name} off" + (
            f" — also turned off {len(changed)} step(s) that need it" if changed else "")
    return {"steps": saved["steps"], "changed": changed, "message": msg}


def explain(flow_path: str, step_index: int) -> list[str]:
    """Why a step is needed: the steps it directly depends on, as file lines."""
    g = FlowGraph(_read_lines(flow_path))
    return [f"line {g.steps[j].line}: {g.steps[j].text[:80]}" for j in sorted(g.needs(step_index))]
