"""
locators/auto_capture.py

Automatic, validated locator capture — no manual clicking.

Closes the gap between the project's two existing generators and what a runnable
locator actually needs. `healing/locator_builder.generate_locator()` and
`spy/server.generate_custom_xpath()` both emit a selector and never check it, so a
colliding selector (e.g. #srchInpId, which matches both the login field and the
site search box) is saved silently and fails only at run time.

Pipeline
--------
  1. resolve   — semantic element name ("MobileNumberInput") → live DOM element
  2. extract   — capture full element DNA in the recorder's schema
  3. generate  — build ranked candidates (reuses healing.locator_builder priorities)
  4. validate  — match count / visible / enabled  ← the missing gate
  5. score     — stability weight (reuses healing.ml_engine.WEIGHTS)
  6. save      — winner + DNA, so ML self-healing has something to match on

Steps 1, 4 and 6 are new; 3 and 5 reuse existing project code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from healing.locator_builder import generate_locator

# Stability weights — single source of truth, reused from the ML engine.
try:
    from healing.ml_engine import MLHealer as _MLH

    WEIGHTS: dict[str, float] = dict(_MLH.WEIGHTS)
except Exception:  # pragma: no cover - fall back only if the class moves
    WEIGHTS = {"data-testid": 5.0, "id": 4.0, "aria-label": 3.5, "name": 3.0,
               "role": 2.5, "type": 2.0, "placeholder": 1.8, "href": 1.5,
               "tag": 1.5, "text": 1.2, "class": 0.8, "position": 0.3}

# Class tokens that never identify an element (utility / state / build-hash).
_BRITTLE = re.compile(
    r"^jsx-|^css-|^sc-|^[0-9a-f]{8,}$|\d{6,}"
    r"|^(font|fw|mb|mt|ml|mr|pb|pt|pl|pr|p|m)-?\d*$"
    r"|^(color|bg)[0-9a-fA-F]{3,}$"
    r"|(disabled|disbld|active|hover|focus|visited|selected|open|show|hide)$",
    re.I,
)

_DATA_TEST_ATTRS = ("data-testid", "data-test-id", "data-test", "data-qa", "data-automation")


def is_stable_token(token: str) -> bool:
    """A class token worth putting in a selector."""
    return bool(token) and not _BRITTLE.search(token.strip())


def split_semantic(name: str) -> list[str]:
    """'MobileNumberInput' / 'mobile_number_input' → ['mobile','number','input']"""
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1 \2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


#: Trailing noun in a semantic name → plausible DOM shapes.
_KIND_HINTS = {
    "input": ("input", "textarea"), "field": ("input", "textarea"),
    "button": ("button", "a", "div", "span"), "btn": ("button", "a", "div", "span"),
    "cta": ("button", "a", "div", "span"), "link": ("a", "button", "span"),
    "title": ("h1", "h2", "h3", "h4", "span", "div", "p"),
    "label": ("label", "span", "div", "p"),
    "text": ("span", "div", "p"), "toaster": ("div", "span", "section"),
    "container": ("div", "section", "form"), "wrapper": ("div", "section"),
    "checkbox": ("input",), "icon": ("i", "span", "svg"),
}


@dataclass
class Candidate:
    rank: int
    kind: str                # data-testid | aria-label | id | name | placeholder | css | xpath
    selector: str
    weight: float = 0.0
    matches: int = -1
    visible: bool = False
    enabled: bool = False
    rejected: str = ""

    @property
    def usable(self) -> bool:
        return self.matches == 1 and self.visible and not self.rejected


# A selector whose identity depends on generated/volatile values — never trust one
# of these without a human confirming it.
_DYNAMIC = re.compile(r"\b\d{4,}\b|[0-9a-f]{8,}|nth-child|\[\d+\]|:nth|uuid|guid", re.I)

#: Below this weight a locator rests on visible text alone, or on a single class,
#: which is where silent breakage comes from. Anything weaker is referred to the
#: operator rather than guessed at. Scale is MLHealer.WEIGHTS: id 4.0,
#: aria-label 3.5, name 3.0, role 2.5, type 2.0, placeholder 1.8, tag 1.5,
#: text 1.2, class 0.8 — so 2.0 admits tag+class (e.g. div.photoreqwrap = 2.3)
#: and refers anything resting on text or a bare class.
UNCERTAIN_BELOW = 2.0


def looks_dynamic(selector: str) -> bool:
    return bool(_DYNAMIC.search(selector or ""))


def find_existing(locator_name: str, selector: str = "") -> dict:
    """
    Reuse check, run BEFORE any capture.

    Two questions: is this name already recorded, and is this selector already
    recorded under a different name? The second catches the duplicate-locator drift
    that makes a repository unmaintainable.
    """
    import json
    import os

    from config import settings

    hit = {"by_name": None, "by_selector": []}
    for path in (settings.RECORDED_ELEMENTS_FILE, settings.MANUAL_LOCATORS_FILE):
        if not os.path.exists(path):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for group, els in (data or {}).items():
            if not isinstance(els, dict):
                continue
            for name, entry in els.items():
                if name.startswith("_") or not isinstance(entry, dict):
                    continue
                existing = (entry.get("custom_xpath") or entry.get("xpath")
                            or entry.get("value") or "")
                if name == locator_name and hit["by_name"] is None:
                    hit["by_name"] = {"group": group, "selector": existing,
                                      "file": os.path.basename(path),
                                      "has_dna": bool(entry.get("tagName"))}
                if selector and existing == selector and name != locator_name:
                    hit["by_selector"].append({"name": name, "group": group,
                                               "file": os.path.basename(path)})
    return hit


@dataclass
class CaptureResult:
    name: str                        # framework locator name, e.g. ask_more_photos_cta
    element_name: str                # semantic source name, e.g. AskMorePhotosCTA
    status: str = "NOT_FOUND"        # CAPTURED | REUSED | NEEDS_USER_INPUT |
                                     # NOT_FOUND | NO_USABLE_CANDIDATE
    chosen: Candidate | None = None
    candidates: list[Candidate] = field(default_factory=list)
    dna: dict = field(default_factory=dict)
    detail: str = ""
    alternates: list = field(default_factory=list)
    existing: dict = field(default_factory=dict)

    def to_entry(self) -> dict:
        """Locator-DB entry: selector for execution, DNA for ML self-healing."""
        if not self.chosen:
            raise ValueError(f"{self.name}: no chosen candidate")
        return {
            "custom_xpath": self.chosen.selector,
            "selectors": ([{"type": self.chosen.kind, "value": self.chosen.selector}]
                          + [{"type": a.kind, "value": a.selector, "weight": a.weight}
                             for a in self.alternates]),
            "last_success": self.chosen.kind,
            "_source_element": self.element_name,
            "_captured_by": "locators/auto_capture.py",
            "_stability_weight": round(self.chosen.weight, 2),
            "_evidence": (f"{self.chosen.matches} match, visible={self.chosen.visible}, "
                          f"enabled={self.chosen.enabled}"),
            # Recorder-schema DNA so healing/ml_engine.py can match on it.
            "tagName": self.dna.get("tagName"),
            "id": self.dna.get("id"),
            "className": self.dna.get("className"),
            "innerText": self.dna.get("innerText"),
            "rect": self.dna.get("rect"),
            "attributes": self.dna.get("attributes", {}),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 1. resolve — find the element in the live DOM from its semantic name
# ─────────────────────────────────────────────────────────────────────────────

_SCAN_JS = """
(cfg) => {
  const {tokens, tags, scope} = cfg;
  const root = scope ? document.querySelector(scope) : document;
  if (!root) return [];
  const out = [];
  root.querySelectorAll('*').forEach(el => {
    const tag = el.tagName.toLowerCase();
    const a = {}; for (const x of el.attributes) a[x.name] = x.value;
    const cs = getComputedStyle(el);
    const r  = el.getBoundingClientRect();
    const visible = cs.display !== 'none' && cs.visibility !== 'hidden'
                    && cs.opacity !== '0' && r.width > 0 && r.height > 0;
    if (!visible) return;
    const own = [...el.childNodes].filter(n => n.nodeType === 3)
                 .map(n => n.textContent).join(' ').trim();
    const full = (el.innerText || '').trim();
    const hay = (own + ' ' + full + ' ' + tag + ' ' + Object.entries(a)
                  .filter(([k]) => k !== 'style')
                  .map(([k, v]) => k + ' ' + v).join(' ')).toLowerCase();
    let hits = 0;
    for (const t of tokens) if (hay.includes(t)) hits++;
    if (!hits) return;
    out.push({
      tagName: tag, id: el.id || null, className: (typeof el.className === 'string' ? el.className : ''),
      innerText: (el.innerText || '').trim().slice(0, 120), ownText: own.slice(0, 120),
      attributes: a, rect: {x: Math.round(r.x), y: Math.round(r.y),
                            width: Math.round(r.width), height: Math.round(r.height)},
      tokenHits: hits, tagMatch: tags.includes(tag), childCount: el.children.length,
      clickable: ['a','button'].includes(tag) || cs.cursor === 'pointer'
                 || !!el.onclick || el.hasAttribute('onclick') || a['role'] === 'button',
      path: (() => { const ix = []; let n = el;
                     while (n && n.parentElement && n !== document.body) {
                       ix.unshift([...n.parentElement.children].indexOf(n)); n = n.parentElement; }
                     return ix; })(),
    });
  });
  return out;
}
"""


_PROMOTE_JS = """
(cfg) => {
  const {path, maxUp} = cfg;
  let el = document.body;
  for (const i of path) { if (!el) return null; el = el.children[i]; }
  if (!el) return null;
  const isClickable = (n) => {
    const cs = getComputedStyle(n);
    return ['a','button'].includes(n.tagName.toLowerCase()) || cs.cursor === 'pointer'
           || !!n.onclick || n.hasAttribute('onclick') || n.getAttribute('role') === 'button';
  };
  let n = el, up = 0;
  while (n && up <= maxUp && n !== document.body) {
    if (isClickable(n)) {
      const a = {}; for (const x of n.attributes) a[x.name] = x.value;
      const r = n.getBoundingClientRect();
      const own = [...n.childNodes].filter(c => c.nodeType === 3)
                   .map(c => c.textContent).join(' ').trim();
      return {tagName: n.tagName.toLowerCase(), id: n.id || null,
              className: (typeof n.className === 'string' ? n.className : ''),
              innerText: (n.innerText || '').trim().slice(0,120), ownText: own.slice(0,120),
              attributes: a, rect: {x: Math.round(r.x), y: Math.round(r.y),
                                    width: Math.round(r.width), height: Math.round(r.height)},
              promotedLevels: up};
    }
    n = n.parentElement; up++;
  }
  return null;
}
"""


def promote_to_clickable(page, descriptor: dict, max_up: int = 5) -> dict | None:
    """
    Walk up to the NEAREST clickable ancestor.

    The element carrying a label is usually not the click target — the text sits in a
    leaf <span> while the handler lives on a wrapper. "Nearest" matters: on the JDMart
    PDP the span's first clickable ancestor is the 125x31 CTA wrapper, while two levels
    higher sits the 412x390 image carousel, which is also clickable and entirely wrong.
    """
    if not descriptor.get("path"):
        return None
    return page.evaluate(_PROMOTE_JS, {"path": descriptor["path"], "maxUp": max_up})


def resolve(page, element_name: str, *, scope: str | None = None,
            text_hint: str | None = None, prefer_clickable: bool | None = None) -> list[dict]:
    """
    Find candidate DOM elements for a semantic name. Returns descriptors, best first.

    Scoring favours: token hits, expected tag shape, leaf-ness (the element that
    *carries* the text rather than an ancestor containing it), and clickability
    when the name implies an action.
    """
    tokens = split_semantic(element_name)
    tail = tokens[-1] if tokens else ""
    tags = list(_KIND_HINTS.get(tail, ()))
    if prefer_clickable is None:
        prefer_clickable = tail in ("button", "btn", "cta", "link")

    found = page.evaluate(_SCAN_JS, {"tokens": tokens, "tags": tags, "scope": scope})

    if text_hint:
        # Search for the hint itself, not just among name-token matches.
        hint_tokens = [t for t in re.split(r"[^A-Za-z0-9']+", text_hint.lower()) if t]
        by_text = page.evaluate(_SCAN_JS, {"tokens": hint_tokens, "tags": tags, "scope": scope})
        seen_paths = {tuple(d.get("path") or []) for d in found}
        for d in by_text:
            if tuple(d.get("path") or []) not in seen_paths:
                found.append(d)

        needle = text_hint.lower()
        exact = [d for d in found if needle == (d["ownText"] or "").strip().lower()]
        if exact:
            found = sorted(exact, key=lambda d: (d["childCount"],
                                                 d["rect"]["width"] * d["rect"]["height"]))
        else:
            partial = [d for d in found if needle in (d["innerText"] or "").lower()]
            found = partial or found

    def rank(d: dict) -> tuple:
        return (
            -d["tokenHits"],
            0 if d["tagMatch"] else 1,
            0 if (not prefer_clickable or d["clickable"]) else 1,
            d["childCount"],                       # prefer the leaf carrying the content
            d["rect"]["width"] * d["rect"]["height"],   # prefer the tighter box
        )

    return sorted(found, key=rank)[:8]


# ─────────────────────────────────────────────────────────────────────────────
# 3. generate — ranked candidates (project priority order)
# ─────────────────────────────────────────────────────────────────────────────

def build_candidates(dna: dict) -> list[Candidate]:
    tag = dna.get("tagName", "*")
    a = dna.get("attributes", {}) or {}
    own = (dna.get("ownText") or dna.get("innerText") or "").strip()
    out: list[Candidate] = []

    for key in _DATA_TEST_ATTRS:
        if a.get(key):
            out.append(Candidate(1, "data-testid", f'[{key}="{a[key]}"]', WEIGHTS["data-testid"]))
    if a.get("aria-label"):
        out.append(Candidate(2, "aria-label", f'[aria-label="{a["aria-label"]}"]', WEIGHTS["aria-label"]))
    if a.get("id"):
        c = Candidate(3, "id", f'#{a["id"]}', WEIGHTS["id"])
        if not is_stable_token(a["id"]):
            c.rejected = "unstable/generated id"
        if a["id"][:1].isdigit():
            c.rejected = "invalid CSS: id starts with a digit"
        out.append(c)
    if a.get("name"):
        out.append(Candidate(4, "name", f'{tag}[name="{a["name"]}"]', WEIGHTS["name"]))
    if a.get("placeholder"):
        out.append(Candidate(5, "placeholder",
                             f'{tag}[placeholder="{a["placeholder"]}"]', WEIGHTS["placeholder"]))

    stable = [c for c in (a.get("class") or "").split() if is_stable_token(c)]
    for n in (1, 2):
        if len(stable) >= n:
            out.append(Candidate(6, "css", tag + "".join(f".{c}" for c in stable[:n]),
                                 WEIGHTS["class"] + WEIGHTS["tag"]))
    if own and len(own) < 40:
        if "'" in own:
            out.append(Candidate(7, "xpath", f'//{tag}[normalize-space()="{own}"]',
                                 WEIGHTS["text"] + WEIGHTS["tag"]))
        else:
            out.append(Candidate(7, "xpath", f"//{tag}[normalize-space()='{own}']",
                                 WEIGHTS["text"] + WEIGHTS["tag"]))

    # Always include the project's own generator output for comparison.
    try:
        legacy = generate_locator(dna)
        if legacy and legacy != f"//{tag}":
            out.append(Candidate(8, "xpath", legacy, WEIGHTS["tag"]))
    except Exception:
        pass

    seen, uniq = set(), []
    for c in out:
        if c.selector not in seen:
            seen.add(c.selector)
            uniq.append(c)
    return uniq


# ─────────────────────────────────────────────────────────────────────────────
# 4. validate — the gate the existing generators lack
# ─────────────────────────────────────────────────────────────────────────────

def _measure(page, selector: str) -> tuple[int, bool, bool]:
    loc = page.locator(selector)
    n = loc.count()
    return n, (loc.first.is_visible() if n else False), (loc.first.is_enabled() if n else False)


def validate(page, cand: Candidate, *, scope: str | None = None) -> Candidate:
    """
    Measure the candidate exactly as it will be stored.

    The selector is checked UNSCOPED first, because that is how the runner resolves
    it. A scope is only a search hint; if the unscoped form is ambiguous and the
    scoped form is unique, the candidate is rewritten to the scoped form so the
    saved selector is the one that actually passed.
    """
    try:
        cand.matches, cand.visible, cand.enabled = _measure(page, cand.selector)
        if cand.matches != 1 and scope and not cand.selector.startswith("//"):
            scoped = f"{scope} {cand.selector}"
            n, vis, en = _measure(page, scoped)
            if n == 1:
                cand.selector, cand.matches, cand.visible, cand.enabled = scoped, n, vis, en
    except Exception as e:
        cand.rejected = cand.rejected or f"{type(e).__name__}"
        cand.matches = -1
    if cand.matches == 0 and not cand.rejected:
        cand.rejected = "no match"
    elif cand.matches > 1 and not cand.rejected:
        cand.rejected = f"not unique ({cand.matches} matches)"
    elif cand.matches == 1 and not cand.visible and not cand.rejected:
        cand.rejected = "not visible"
    return cand


# ─────────────────────────────────────────────────────────────────────────────
# capture — the whole pipeline for one element
# ─────────────────────────────────────────────────────────────────────────────

def capture(page, locator_name: str, element_name: str, *,
            scope: str | None = None, text_hint: str | None = None,
            reuse: bool = True, uncertain_below: float = UNCERTAIN_BELOW) -> CaptureResult:
    res = CaptureResult(name=locator_name, element_name=element_name)

    # ── Reuse before capture ────────────────────────────────────────────────
    if reuse:
        res.existing = find_existing(locator_name)
        prior = res.existing.get("by_name")
        if prior and prior.get("selector"):
            n, vis, _en = _measure(page, prior["selector"])
            if n == 1 and vis:
                res.status = "REUSED"
                res.detail = (f"already recorded in {prior['file']} / {prior['group']} "
                              f"as {prior['selector']!r} — still unique and visible, kept as is"
                              + ("" if prior["has_dna"] else "  [no DNA: healing unavailable]"))
                return res
            res.detail = (f"recorded selector {prior['selector']!r} no longer resolves "
                          f"(n={n}, visible={vis}) — re-capturing")

    matches = resolve(page, element_name, scope=scope, text_hint=text_hint)
    if not matches:
        res.detail = f"no visible element matched tokens {split_semantic(element_name)}"
        return res

    res.dna = matches[0]

    tail = (split_semantic(element_name) or [""])[-1]
    if tail in ("button", "btn", "cta", "link") and not res.dna.get("clickable"):
        promoted = promote_to_clickable(page, res.dna)
        if promoted:
            res.detail = (f"promoted {promoted['promotedLevels']} level(s) from "
                          f"<{res.dna['tagName']}> to clickable <{promoted['tagName']}> "
                          f"{promoted['rect']['width']}x{promoted['rect']['height']}")
            res.dna = promoted
    res.candidates = [validate(page, c, scope=scope) for c in build_candidates(res.dna)]

    usable = [c for c in res.candidates if c.usable]
    if not usable:
        res.status = "NO_USABLE_CANDIDATE"
        res.detail = "; ".join(f"{c.selector} -> {c.rejected}" for c in res.candidates[:4])
        return res

    # Best = highest stability weight, then the project's own priority rank.
    ranked = sorted(usable, key=lambda c: (-c.weight, c.rank))
    res.chosen = ranked[0]
    res.alternates = ranked[1:4]          # kept for when the primary breaks later
    res.status = "CAPTURED"

    # ── Refer anything unsafe to the operator instead of guessing ───────────
    reasons = []
    if looks_dynamic(res.chosen.selector):
        reasons.append("selector contains generated/volatile values")
    if res.chosen.weight < uncertain_below:
        reasons.append(f"stability weight {res.chosen.weight} is below {uncertain_below} "
                       f"(rests on tag/text/class alone)")
    ties = [c for c in ranked if c.weight == res.chosen.weight]
    if len(ties) > 1 and res.chosen.kind in ("css", "xpath"):
        reasons.append(f"{len(ties)} candidates tie at weight {res.chosen.weight}")
    if reasons:
        res.status = "NEEDS_USER_INPUT"
        res.detail = "; ".join(reasons)

    dup = find_existing(locator_name, res.chosen.selector).get("by_selector") or []
    if dup:
        res.detail = ((res.detail + " | ") if res.detail else "") + \
            "same selector already saved as " + ", ".join(f"{d['name']} ({d['group']})" for d in dup)
    if len(matches) > 1 and matches[0]["tokenHits"] == matches[1]["tokenHits"]:
        res.detail = f"note: {len(matches)} elements matched the name; took the best-ranked"
    return res


def save(results: list[CaptureResult], group: str, path: str | None = None) -> dict:
    """Write captured locators (selector + DNA) into the manual locator DB."""
    import json
    import os

    from config import settings

    path = path or settings.MANUAL_LOCATORS_FILE
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    grp = data.setdefault(group, {})
    written = {}
    for r in results:
        if r.status != "CAPTURED":
            continue
        grp[r.name] = r.to_entry()
        written[r.name] = r.chosen.selector
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return written


def report(results: list[CaptureResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"\n{r.name}  ({r.element_name})   [{r.status}]")
        if r.detail:
            lines.append(f"  {r.detail}")
        if r.dna:
            lines.append(f"  element : <{r.dna.get('tagName')}> "
                         f"{r.dna.get('rect', {}).get('width')}x{r.dna.get('rect', {}).get('height')} "
                         f"text={ (r.dna.get('ownText') or '')[:34]!r}")
        for c in r.candidates:
            mark = "CHOSEN" if c is r.chosen else ("ok" if c.usable else "reject")
            lines.append(f"    [{mark:<6}] w={c.weight:<4} {c.kind:<12} {c.selector[:46]:<46} "
                         f"n={c.matches} vis={c.visible} en={c.enabled}"
                         + (f"  <- {c.rejected}" if c.rejected else ""))
    return "\n".join(lines)
