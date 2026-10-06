"""
locators/ai_capture.py — record an element the first time a run needs it.

A drafted test case names elements that nobody has recorded yet
(`pdp_photo_tile_360_icon`). Until now the run stopped there and someone had
to open the page, find the node and record it by hand. Now, when a run has
"Record missing elements with AI" on, the engine:

  1. snapshots the page the step is looking at (visible, meaningful nodes with
     their text, ids, stable classes, roles, data-* attributes and a position
     path — nothing else, no scripts, no styles),
  2. asks the model which node the element name + step mean, and for an XPath
     written from the attributes it can see,
  3. validates that XPath on the live page (must match, must be visible — a
     second round with the model if it does not),
  4. saves it under a per-feature group in locators_manual.json, tagged with the
     platform, with the node's DNA so self-healing can match it later,
  5. hands the locator back to the step, which continues as if it had always
     been there.

Every recorded element is logged with the reason the model gave, so a wrong
pick is visible in the run log and easy to re-record from the Elements page.
Never raises into the run: when it cannot decide, the step fails exactly as it
did before, with the model's explanation in the error.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

#: Per-thread context for the current run: platform, flow name, step text.
_tl = threading.local()

MAX_NODES = 320           # nodes shown to the model
MAX_TEXT = 60


# ─────────────────────────────────────────────────────────────────────────────
# Run context
# ─────────────────────────────────────────────────────────────────────────────
def enable(platform: str, flow_name: str, group: str | None = None) -> None:
    _tl.ctx = {"platform": platform, "flow": flow_name,
               "group": group or _group_for(flow_name, platform), "step": "", "tried": set()}


def disable() -> None:
    _tl.ctx = None


def set_step(step_text: str) -> None:
    ctx = getattr(_tl, "ctx", None)
    if ctx is not None:
        ctx["step"] = step_text or ""


def active() -> bool:
    return bool(getattr(_tl, "ctx", None))


def _group_for(flow_name: str, platform: str) -> str:
    """tc_bwn1471_01 → ai_bwn1471_website; PDP_Need_Assistance → ai_pdp_need_assistance_mobilesite."""
    base = re.sub(r"_\d+$", "", (flow_name or "flow").lower())
    base = re.sub(r"^(tc|ts)_", "", base)
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:40] or "elements"
    return f"ai_{base}_{platform or 'website'}"


# ─────────────────────────────────────────────────────────────────────────────
# Page snapshot
# ─────────────────────────────────────────────────────────────────────────────
_SNAPSHOT_JS = r"""(limit) => {
  const skip = new Set(['SCRIPT','STYLE','NOSCRIPT','META','LINK','HEAD','TITLE','BR','HR','SVG','PATH','G']);
  const brittle = /^(jsx-|css-|sc-)|^[0-9a-f]{8,}$|\d{6,}|^(font|fw|mb|mt|ml|mr|pb|pt|pl|pr|p|m)-?\d*$|^(color|bg)[0-9a-f]{3,}$|(active|hover|focus|visited|selected|open|show|hide|disabled)$/i;
  const out = [];
  const all = document.querySelectorAll('body *');
  const path = (el) => {
    const parts = [];
    while (el && el.nodeType === 1 && el.tagName !== 'BODY') {
      let i = 1, s = el.previousElementSibling;
      while (s) { if (s.tagName === el.tagName) i++; s = s.previousElementSibling; }
      parts.unshift(el.tagName.toLowerCase() + '[' + i + ']');
      el = el.parentElement;
    }
    return '/html/body/' + parts.join('/');
  };
  for (const el of all) {
    if (out.length >= limit * 3) break;
    if (skip.has(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) continue;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity) === 0) continue;
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).filter(Boolean).join(' ');
    const cls = (typeof el.className === 'string' ? el.className : '').split(/\s+/).filter(c => c && !brittle.test(c)).slice(0, 4);
    const attrs = {};
    for (const a of el.attributes) {
      const n = a.name;
      if (n === 'id' || n === 'role' || n === 'aria-label' || n === 'name' || n === 'placeholder' || n === 'type' || n === 'alt' || n === 'title' || n.startsWith('data-at') || n.startsWith('data-test') || n === 'data-qa') {
        if (a.value && a.value.length <= 80) attrs[n] = a.value;
      }
      if ((n === 'href' || n === 'src') && a.value) attrs[n] = a.value.slice(-60);
      if (n === 'style' && /background(-image)?\s*:\s*url/.test(a.value)) { const m = a.value.match(/url\(["']?([^"')]+)/); if (m) attrs['bg'] = m[1].slice(-50); }
    }
    const kids = el.children.length;
    // keep leaves, interactive nodes, and containers with an id / stable class
    const interactive = /^(A|BUTTON|INPUT|SELECT|TEXTAREA|LABEL|IMG|LI|H1|H2|H3|H4|SPAN|I|SECTION)$/.test(el.tagName) || el.getAttribute('role') || el.onclick;
    if (!own && !interactive && !attrs.id && !cls.length) continue;
    out.push({i: out.length, tag: el.tagName.toLowerCase(), cls, attrs, text: own.slice(0, 60), kids,
              box: [Math.round(r.left), Math.round(r.top + window.scrollY), Math.round(r.width), Math.round(r.height)],
              path: path(el)});
  }
  return out;
}"""


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) >= 2}


def snapshot(page, element_name: str, step: str) -> list[dict]:
    """Visible nodes, most relevant to the element name first, capped."""
    try:
        nodes = page.evaluate(_SNAPSHOT_JS, MAX_NODES) or []
    except Exception as e:  # noqa: BLE001
        logger.debug("AI capture: snapshot failed: %s", e)
        return []
    want = _tokens(element_name) | _tokens(step)
    # synonyms a tester's name uses for what the DOM calls something else
    syn = {"icon": {"icn", "ico", "img", "badge"}, "section": {"sec", "wrap", "wrp", "container", "cont"},
           "button": {"btn"}, "image": {"img", "photo"}, "photos": {"photo", "gallery", "gly", "image"},
           "tile": {"img", "thumb", "card", "cell"}, "360": {"360", "3d", "threesixty", "panorama"},
           "back": {"back", "close", "prev", "arrow"}, "popup": {"popup", "modal", "sheet", "dialog"}}
    for t in list(want):
        want |= syn.get(t, set())

    def score(n: dict) -> int:
        hay = " ".join([n.get("tag", ""), " ".join(n.get("cls") or []), n.get("text", ""),
                        " ".join(f"{k} {v}" for k, v in (n.get("attrs") or {}).items())]).lower()
        toks = _tokens(hay)
        s = sum(2 for t in want if t in toks) + sum(1 for t in want if len(t) >= 3 and t in hay)
        if n.get("attrs", {}).get("id"):
            s += 1
        return s

    nodes.sort(key=lambda n: (-score(n), n["i"]))
    keep = nodes[:MAX_NODES]
    keep.sort(key=lambda n: n["i"])          # document order again, for context
    return keep


# ─────────────────────────────────────────────────────────────────────────────
# Ask the model
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM = """You are recording web elements for a codeless test-automation tool. \
A test step refers to an element by a descriptive name that nobody has recorded yet. \
You get a snapshot of the page the step is looking at: visible nodes with tag, stable \
class tokens, a few attributes, their own text, child count, bounding box [left, top, \
width, height] in page pixels and an absolute position path.

Decide which node the name + step mean and write an XPath for it:
- Prefer attributes that describe the element (id, data-at, data-testid, aria-label, \
stable class tokens, visible text). Never use brittle classes (hashes, spacing/colour \
utilities) or long absolute paths unless nothing else identifies the node.
- For a group of similar things ("photo tile", "first card") pick the FIRST matching \
node and write an XPath that matches that one, e.g. (//div[contains(@class,'photos_tab_imgcontainer')])[1].
- For "<x> icon" inside "<y> tile" style names, the icon is usually a small child node \
with a 360/3d/badge class or background image; the tile is its container.
- A "section" / "heading" is the container or heading element of that area.
- If the page clearly does not contain what the name describes (wrong page, not loaded, \
nothing like it in the snapshot), say so with found=false and a one-line reason — do \
not guess a random node.
Return: found, xpath, node_index (the snapshot index you chose, or -1), reason (one line)."""


def _ask(provider, element_name: str, step: str, flow: str, nodes: list[dict], feedback: str = "") -> dict:
    from pydantic import BaseModel

    class Pick(BaseModel):
        found: bool
        xpath: str = ""
        node_index: int = -1
        reason: str = ""

    lines = []
    for n in nodes:
        a = n.get("attrs") or {}
        bits = [f"#{n['i']}", n["tag"]]
        if n.get("cls"):
            bits.append("." + ".".join(n["cls"]))
        for k, v in a.items():
            bits.append(f"{k}={v!r}")
        if n.get("text"):
            bits.append(f"text={n['text']!r}")
        bits.append(f"kids={n.get('kids', 0)} box={n.get('box')}")
        bits.append(f"path={n['path']}")
        lines.append(" ".join(bits))
    user = (f"Test case: {flow}\nStep: {step or '(unknown)'}\nElement name: {element_name}\n"
            + (f"\nPrevious attempt failed: {feedback}\n" if feedback else "")
            + f"\nPage snapshot ({len(nodes)} nodes, document order):\n" + "\n".join(lines))
    c = provider.complete_structured(system=_SYSTEM, user=user, schema=Pick)
    return dict(c.data)


# ─────────────────────────────────────────────────────────────────────────────
# Validate + save
# ─────────────────────────────────────────────────────────────────────────────
def _measure(page, xpath: str) -> tuple[int, bool]:
    try:
        loc = page.locator(f"xpath={xpath}" if not xpath.startswith(("xpath=", "css=")) else xpath)
        n = loc.count()
        vis = False
        for k in range(min(n, 5)):
            try:
                if loc.nth(k).is_visible(timeout=300):
                    vis = True
                    break
            except Exception:  # noqa: BLE001
                continue
        return n, vis
    except Exception:  # noqa: BLE001
        return -1, False


def _dna(page, xpath: str) -> dict:
    try:
        return page.locator(f"xpath={xpath}").first.evaluate("""el => ({
            tagName: el.tagName.toLowerCase(), id: el.id || '', className: (typeof el.className === 'string' ? el.className : ''),
            innerText: (el.innerText || '').trim().slice(0, 80), name: el.getAttribute('name') || '',
            ariaLabel: el.getAttribute('aria-label') || '', role: el.getAttribute('role') || '',
            placeholder: el.getAttribute('placeholder') || '', type: el.getAttribute('type') || ''})""") or {}
    except Exception:  # noqa: BLE001
        return {}


def save(name: str, xpath: str, *, group: str, platform: str, reason: str, dna: dict,
         flow: str, step: str) -> None:
    from config import settings
    from locators.io_utils import atomic_write_json, file_lock, read_json

    path = settings.MANUAL_LOCATORS_FILE
    with file_lock(path, exclusive=True):
        data = read_json(path) if os.path.exists(path) else {}
        grp = data.setdefault(group, {})
        grp["_platform"] = platform
        grp[name] = {
            "custom_xpath": xpath,
            "selectors": [{"type": "xpath", "value": xpath}],
            "last_success": "xpath",
            "_captured_by": "locators/ai_capture.py",
            "_recorded_from": {"test_case": flow, "step": step[:160]},
            "_reason": reason[:300],
            **({"dna": dna} if dna else {}),
        }
        atomic_write_json(path, data)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point used by the locator manager
# ─────────────────────────────────────────────────────────────────────────────
def capture(page, element_name: str) -> tuple[str, dict] | None:
    """Record `element_name` on `page`. Returns (xpath, dna) or None."""
    ctx = getattr(_tl, "ctx", None)
    if not ctx or page is None or element_name in ctx["tried"]:
        return None
    ctx["tried"].add(element_name)
    step = ctx.get("step", "")
    try:
        from ai_flow_builder.llm import get_provider
        provider = get_provider()
    except Exception as e:  # noqa: BLE001
        logger.warning("🧠 AI capture off — no model configured: %s", e)
        return None
    nodes = snapshot(page, element_name, step)
    if not nodes:
        logger.warning("🧠 AI capture: nothing visible on the page to record '%s' from", element_name)
        return None
    feedback = ""
    for attempt in (1, 2):
        try:
            pick = _ask(provider, element_name, step, ctx["flow"], nodes, feedback)
        except Exception as e:  # noqa: BLE001
            logger.warning("🧠 AI capture: model error for '%s': %s", element_name, e)
            return None
        if not pick.get("found"):
            logger.warning("🧠 AI capture: '%s' not on this page — %s", element_name, pick.get("reason", ""))
            ctx.setdefault("why", {})[element_name] = pick.get("reason", "")
            return None
        xpath = (pick.get("xpath") or "").strip()
        n, vis = _measure(page, xpath) if xpath else (-1, False)
        if n == 0 or n < 0:
            # fall back to the chosen node's position path
            idx = pick.get("node_index", -1)
            node = next((x for x in nodes if x["i"] == idx), None)
            if node:
                xpath = node["path"]
                n, vis = _measure(page, xpath)
        if n >= 1 and vis:
            dna = _dna(page, xpath)
            save(element_name, xpath, group=ctx["group"], platform=ctx["platform"],
                 reason=pick.get("reason", ""), dna=dna, flow=ctx["flow"], step=step)
            logger.info("🧠 AI recorded '%s' → %s  (%d match%s; %s)", element_name, xpath, n,
                        "" if n == 1 else "es", pick.get("reason", "")[:120])
            return xpath, dna
        feedback = (f"XPath {xpath!r} matched {max(n, 0)} node(s)"
                    f"{' but none visible' if n >= 1 else ''}. Choose again from the snapshot "
                    f"and write an XPath that matches at least one visible node.")
    logger.warning("🧠 AI capture: could not record '%s' after 2 attempts", element_name)
    return None
