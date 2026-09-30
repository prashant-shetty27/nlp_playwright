"""
ai_flow_builder/review.py — read a test and say how to make it better.

Most of what makes a test slow, brittle or unmaintainable is visible in its own
text, and does not need a model to spot:

  * a mobile number typed into a step when the same number is already stored
    under a name — the value is now in two places, and in a file that gets
    committed;
  * a fixed `wait 8 seconds` standing in for "wait until the thing appears",
    which is both slower than it needs to be and still flaky on a slow day;
  * the same three opening steps at the top of nine tests, which is a step group
    waiting to be made;
  * a test that clicks through six screens and never asserts anything.

So the checks here are DETERMINISTIC. They run in milliseconds, cost nothing,
give the same answer twice, and — the part that matters — can carry the exact
change, so a finding can be acted on rather than just read.

A finding carries whichever of three shapes fits it, and the editor shows the
step itself next to the button that applies it:

  fix       rewrite this step as ...        (a hardcoded value, a fixed wait)
  add_step  put THIS step in at position N  (a missing wait after a navigation)
  remove    take this step out              (a duplicate, a redundant wait)

Suggestions are written in the target platform's own vocabulary — the web and
Appium runners have separate dispatch tables — so an accepted suggestion runs
rather than failing later with "Unknown command type".

Each finding says what, where, why, and (where it can) the change itself. A
finding without a defensible "why" is noise, and noise is how a review screen
comes to be ignored.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

#: A value long enough to be worth naming. Two-digit numbers are usually counts.
_MIN_VALUE_LEN = 4


#: What the reader is being asked to DO, which is not the same as how bad it is.
#:
#:   must     it is broken, or it reports success when it should not. Fix it.
#:   can      it works, but there is a better way — usually faster or steadier.
#:   optional style, or something that may well be deliberate. Take it or leave it.
#:
#: The third bucket exists because a review that presents every observation as a
#: defect gets ignored wholesale. A fixed wait after submitting an OTP is often
#: there on purpose; saying so, instead of demanding it be changed, is the
#: difference between a reviewer and a linter.
BUCKETS = ("must", "can", "optional")

BUCKET_LABEL = {
    "must": "Must do — broken, or passing when it should not",
    "can": "Can do — works, but there is a better way",
    "optional": "Not necessary — your call, and may well be deliberate",
}


@dataclass
class Finding:
    kind: str
    severity: str                 # "high" | "medium" | "low"
    step_index: int               # 1-based; 0 = the test as a whole
    message: str
    why: str
    #: A ready replacement for this step, when one can be derived.
    fix: str = ""
    #: A ready NEW step, when the answer is to insert one rather than rewrite
    #: one. Written in the platform's own vocabulary, so it parses and the
    #: platform's runner can dispatch it.
    add_step: str = ""
    #: 1-based position the new step should occupy — it lands there and pushes
    #: the rest down. Meaningless without add_step.
    add_at: int = 0
    #: True when the advice is to take this step OUT. Carried rather than
    #: described, so a redundant step is one click rather than a paragraph.
    remove: bool = False
    #: Extra steps a fix would introduce (a step group, for instance).
    extra: list = field(default_factory=list)
    #: must | can | optional — see BUCKETS.
    bucket: str = "can"
    #: Why this was placed in that bucket, when the reason is not obvious.
    bucket_note: str = ""
    #: The alternative approaches worth knowing about, best first.
    options: list = field(default_factory=list)
    #: A replacement with one blank — "{}" — for the element the reviewer could
    #: not work out. The editor asks for the element and fills it in, so a
    #: finding with a clear fix but an unknown target still gets a button.
    fix_template: str = ""


def _stored_values(environment: str = "") -> dict:
    """{value: name} for everything in the test-data store, longest value first."""
    try:
        from execution.test_data import get_all

        pairs = [(e.get("value", ""), n) for n, e in get_all(environment).items()
                 if e.get("value") and len(str(e["value"])) >= _MIN_VALUE_LEN]
        return {v: n for v, n in sorted(pairs, key=lambda kv: -len(kv[0]))}
    except Exception:  # noqa: BLE001
        return {}


def _looks_sensitive(value: str) -> str:
    """What KIND of value this looks like, or "" — used to explain the finding."""
    v = value.strip()
    if re.fullmatch(r"\d{10}", v):
        return "a mobile number"
    if re.fullmatch(r"\d{4,8}", v):
        return "an OTP or PIN"
    if re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.-]+", v):
        return "an email address"
    if re.match(r"https?://|www\.", v):
        return "a URL"
    return ""


def _is_appium(platform: str) -> bool:
    """
    Whether this platform runs through Appium rather than Playwright.

    It decides the VOCABULARY a suggestion is written in. The two runners have
    separate dispatch tables — `wait until element x is visible` exists only on
    web, `wait for element x` only on Appium — so a suggestion written in the
    wrong one parses cleanly and then dies at run time with "Unknown command
    type", which is a worse outcome than having made no suggestion at all.
    """
    try:
        from nlp.platforms import PLATFORMS, normalise

        return PLATFORMS[normalise(platform)].is_appium
    except Exception:  # noqa: BLE001
        return False


def _wait_for(element: str, appium: bool) -> str:
    """A condition-based wait for `element`, in this platform's own wording."""
    return (f"wait for element {element}" if appium
            else f"wait until element {element} is visible")


#: The ways a step names the element it acts on. One definition, because both
#: the fixed-wait check and the race check need to answer "what is this step
#: waiting for?" and a second copy would drift.
_TARGETED = (
    re.compile(r"^\s*(?:click|tap|fill|clear|scroll to|hover(?: over)?|double click)"
               r"(?:\s+(?:if visible|element|on))*\s+([a-z_][a-z0-9_]*)\s*$", re.I),
    re.compile(r"^\s*(?:type|enter)(?:\s+if visible)?\s+\"[^\"]*\"\s+into\s+"
               r"([a-z_][a-z0-9_]*)\s*$", re.I),
    re.compile(r"^\s*verify\s+(?:that\s+)?(?:element\s+)?([a-z_][a-z0-9_]*)\s+"
               r"(?:is|has|contains|displays|text)\b", re.I),
    re.compile(r"^\s*verify\s+element\s+([a-z_][a-z0-9_]*)\b", re.I),
    re.compile(r"^\s*verify\s+(?:the\s+)?text\s+of\s+([a-z_][a-z0-9_]*)\b", re.I),
    re.compile(r"^\s*select\s+option\s+\"[^\"]*\"\s+in\s+([a-z_][a-z0-9_]*)\s*$", re.I),
    re.compile(r"^\s*swipe\b.*\buntil\s+(?:element\s+)?([a-z_][a-z0-9_]*)\s+is\s+visible", re.I),
)


def _target_of(step: str) -> str:
    """The element this step acts on, or "" when it does not name one."""
    for rx in _TARGETED:
        m = rx.match(step or "")
        if m:
            return m.group(1)
    return ""


def _to_raw_positions(findings: list[Finding], live_pairs: list[tuple[int, str]],
                      total: int) -> None:
    """
    Translate live-list positions back to positions in the file.

    Every check reads the LIVE steps — comments, purpose bands and switched-off
    steps removed — but the editor holds the raw list and indexes straight into
    it. So a live index handed back unchanged pointed at the wrong line in any
    test carrying a band, and Apply rewrote a step nobody had complained about.
    """
    n = len(live_pairs)
    for f in findings:
        if f.step_index and n:
            f.step_index = live_pairs[min(f.step_index, n) - 1][0]
        if f.add_at:
            # Past the last live step means "after everything" — which in the
            # raw list is the end of the file, not the end of the live steps.
            f.add_at = (live_pairs[f.add_at - 1][0] if 0 < f.add_at <= n
                        else total + 1)


def review(steps: list[str], *, platform: str = "website",
           environment: str = "", flow_name: str = "") -> list[Finding]:
    """Every finding for this test, most serious first."""
    out: list[Finding] = []
    # Position is carried alongside the text so a finding can be pointed back at
    # the line the editor actually holds — see _to_raw_positions.
    live_pairs = [(i, s) for i, s in enumerate(steps, 1)
                  if s.strip() and not s.strip().startswith("#")]
    live = [s for _, s in live_pairs]
    stored = _stored_values(environment)
    appium = _is_appium(platform)

    out += _hardcoded_values(live, stored)
    out += _fixed_waits(live, appium)
    out += _repeated_blocks(live, platform)
    out += _no_assertion(live)
    out += _duplicate_steps(live)
    out += _unknown_locators(live, platform)
    out += _element_quality(live, platform)
    # ── the senior-reviewer passes ───────────────────────────────────────────
    out += _race_after_navigation(live, appium)
    out += _redundant_waits(live)
    out += _raw_selectors(live)
    out += _assertion_placement(live)
    out += _shadowed_elements(live, platform)
    out += _length_and_shape(live)
    out += _code_in_steps(live)
    out += _block_structure(live)

    for f in out:
        if not f.bucket_note:
            f.bucket = _MUST if f.kind in _BROKEN else (
                _OPTIONAL if f.severity == "low" else "can")
    _to_raw_positions(out, live_pairs, len(steps))
    order = {"must": 0, "can": 1, "optional": 2}
    return sorted(out, key=lambda f: (order.get(f.bucket, 3), f.step_index))


_MUST, _OPTIONAL = "must", "optional"

#: Findings that mean the test is broken, or passes when it should not.
_BROKEN = {"unknown_element", "no_assertion", "assertion_placement", "code_in_step",
           "shadowed_element", "hardcoded_value", "block_structure"}


# ── individual checks ─────────────────────────────────────────────────────────

def _hardcoded_values(steps: list[str], stored: dict) -> list[Finding]:
    """
    A literal in a step that is already stored under a name.

    This is the one worth fixing first. The value is duplicated, so changing the
    test number means finding every copy; and it is written into a file that is
    committed, so a mobile number or an OTP ends up in the repository, in the
    report and in any screenshot of the editor.
    """
    out: list[Finding] = []
    for i, step in enumerate(steps, 1):
        for value, name in stored.items():
            if value and value in step:
                out.append(Finding(
                    kind="hardcoded_value", severity="high", step_index=i,
                    message=f"This step contains a value you already store as "
                            f"${{{name}}}.",
                    why="Written out in full it lives in two places, so changing "
                        "it means finding every copy — and it goes into the "
                        "committed file, the report and any screenshot.",
                    fix=step.replace(value, "${" + name + "}")))
                break
        else:
            # Not stored yet, but clearly a value that ought to be.
            for m in re.finditer(r'"([^"]{4,})"|(?<![\w/.-])(\d{6,12})(?![\w.-])', step):
                literal = m.group(1) or m.group(2) or ""
                kind = _looks_sensitive(literal)
                if kind:
                    out.append(Finding(
                        kind="unstored_value", severity="medium", step_index=i,
                        message=f"{literal} looks like {kind}, typed straight into "
                                f"the step.",
                        why="Save it once under Test Data and every test can use "
                            "${name} instead. The value then changes in one place "
                            "and stays out of the test file.",
                        fix=""))
                    break
    return out


def _fixed_waits(steps: list[str], appium: bool = False) -> list[Finding]:
    """
    A fixed sleep — but only complained about when the context suggests it is
    standing in for a wait, not when it looks deliberate.

    Some sleeps are meant: after submitting an OTP, after a payment redirect,
    after firing an API call, there may be nothing on the page to wait FOR. A
    reviewer who cannot tell those apart is one whose advice gets skipped
    wholesale, so those land in "not necessary" with the reason stated, and only
    the ones that clearly stand in for a condition are pressed.
    """
    out: list[Finding] = []
    #: Steps after which a pause is usually intentional — an async process the
    #: page gives no signal for.
    DELIBERATE = re.compile(
        r"\b(otp|payment|pay|checkout|submit|api |upload|download|refresh|"
        r"press enter|redirect|logout|sign out|email|sms)\b", re.I)

    for i, step in enumerate(steps, 1):
        m = re.match(r"^\s*wait\s+(\d+(?:\.\d+)?)\s*seconds?\s*$", step, re.I)
        if not m:
            continue
        seconds = float(m.group(1))
        if seconds < 3:
            continue                      # a short settle is not worth a note

        prev = steps[i - 2] if i >= 2 else ""
        nxt = steps[i] if i < len(steps) else ""
        # The step straight after the wait is usually what it waits for, but
        # a "click if visible" closer or a comment can sit in between — so
        # look a few steps on before giving up on naming the element.
        target = ""
        for later in steps[i:i + 3]:
            if re.match(r"^\s*wait\s+\d", later, re.I):
                break
            target = _target_of(later)
            if target:
                break

        # A negative check ("… is not visible", "page does not contain …")
        # is exactly the case where there is nothing to wait FOR: waiting
        # until the element appears would fail the very test that proves it
        # must not appear. The fixed wait is the right tool there.
        if re.search(r"\b(is\s+not\s+visible|not\s+present|does\s+not\s+contain|"
                     r"was\s+not\s+sent|is\s+not\s+shown)\b", nxt, re.I):
            continue
        deliberate = bool(DELIBERATE.search(prev.replace("_", " ")))
        options = [
            {"approach": _wait_for(target, appium) if target
             else _wait_for("<the thing you need>", appium),
             "note": "Playwright polls and continues the moment it appears, so a "
                     "quick page costs almost nothing and a slow one still passes.",
             "best": True},
            {"approach": "leave the fixed wait",
             "note": "Right when there is nothing on the page to wait for — an OTP "
                     "being sent, a payment redirect, a background job.",
             "best": deliberate},
        ]

        if deliberate:
            out.append(Finding(
                kind="fixed_wait", severity="low", step_index=i,
                message=f"Fixed {int(seconds)}s wait after “{prev.strip()[:40]}”.",
                why="This may well be deliberate — there is often nothing on the "
                    "page to wait for after that step. Left as-is unless you know "
                    "of an element that appears when it finishes.",
                bucket="optional",
                bucket_note="the preceding step suggests the pause is intentional",
                options=options, fix=""))
            continue

        out.append(Finding(
            kind="fixed_wait", severity="medium", step_index=i,
            message=f"Waiting a fixed {int(seconds)} seconds.",
            why="It costs the full time on every run even when the page is ready in "
                "one second, and it still fails on a slow day. Waiting for the "
                "thing you actually need is faster AND steadier.",
            bucket="can", options=options,
            fix=_wait_for(target, appium) if target else "",
            fix_template="" if target else _wait_for("{}", appium)))
    return out


def _repeated_blocks(steps: list[str], platform: str) -> list[Finding]:
    """
    The same run of steps appearing in several tests — a step group waiting to
    be made. Compares against every other saved test on this platform, because a
    sequence repeated once inside one test is not yet worth naming.
    """
    out: list[Finding] = []
    try:
        import os

        from config import settings

        flows_dir = os.path.join(settings.BASE_DIR, "flows")
        others: list[list[str]] = []
        for fname in os.listdir(flows_dir):
            if not fname.endswith(".flow"):
                continue
            with open(os.path.join(flows_dir, fname), "r", encoding="utf-8") as f:
                body = [l.strip() for l in f
                        if l.strip() and not l.strip().startswith("#")]
            if body and body != steps:
                others.append(body)
    except Exception:  # noqa: BLE001
        return out

    for size in (4, 3):
        for start in range(0, max(0, len(steps) - size + 1)):
            block = [s.strip() for s in steps[start:start + size]]
            shared = sum(1 for o in others
                         if any(o[j:j + size] == block for j in range(len(o) - size + 1)))
            if shared >= 1:
                out.append(Finding(
                    kind="repeated_block", severity="medium",
                    step_index=start + 1,
                    message=f"These {size} steps also appear in {shared} other "
                            f"test case(s).",
                    why="Saved as a step group they are written once and called by "
                        "name, so a change to the login flow is one edit rather "
                        "than one per test.",
                    fix="", extra=block))
                return out          # one suggestion is enough; more is nagging
    return out


def _no_assertion(steps: list[str]) -> list[Finding]:
    """A test that never checks anything can only fail by crashing."""
    if not steps:
        return []
    if any(re.match(r"^\s*verify\b", s, re.I) for s in steps):
        return []
    return [Finding(
        kind="no_assertion", severity="high", step_index=0,
        message="This test never verifies anything.",
        why="It can only fail if a step errors. If the page loads but shows the "
            "wrong thing, it passes — which is worse than having no test, because "
            "it reports success.",
        fix="")]


def _duplicate_steps(steps: list[str]) -> list[Finding]:
    out: list[Finding] = []
    for i in range(1, len(steps)):
        if steps[i].strip() and steps[i].strip() == steps[i - 1].strip():
            out.append(Finding(
                kind="duplicate_step", severity="low", step_index=i + 1,
                message="Identical to the step above it.",
                why="Almost always a paste that was not meant to happen; if it is "
                    "deliberate, a comment saying why would help the next reader.",
                fix="", remove=True))
    return out


def _unknown_locators(steps: list[str], platform: str) -> list[Finding]:
    """A step naming an element that does not exist cannot run."""
    out: list[Finding] = []
    try:
        from locators.sources import names
        from nlp.fields import TARGET_IS_LOCATOR
        from nlp.parser import parse_step

        known = names(platform)
    except Exception:  # noqa: BLE001
        return out

    for i, step in enumerate(steps, 1):
        try:
            cmd = parse_step(step)
        except Exception:  # noqa: BLE001
            continue
        target = getattr(cmd, "target", None)
        if cmd.type == "block":
            from execution.control_flow import element_names
            for name in element_names(step):
                if name not in known:
                    out.append(Finding(
                        kind="unknown_element", severity="high", step_index=i,
                        message=f"'{name}' is not in your element list.",
                        why="A condition on an element that does not exist is never "
                            "true, so this block would silently never run (or never "
                            "stop). Record it with the spy, or fix the name.", fix=""))
            continue
        if (cmd.type in TARGET_IS_LOCATOR and isinstance(target, str)
                and target and target not in known
                and not target.startswith(("//", "(", "#", ".", "css=", "xpath="))):
            out.append(Finding(
                kind="unknown_element", severity="high", step_index=i,
                message=f"'{target}' is not in your element list.",
                why="This step will fail at run time. Record it with the spy, or "
                    "add it from the editor by clicking the element name.",
                fix=""))
    return out


def _element_quality(steps: list[str], platform: str) -> list[Finding]:
    """
    The elements this test case uses, judged by the element review: a junk
    name, a name defined twice, a blank or position-only selector. One
    finding per element, at the first step that uses it, with the same
    wording as the Elements page so the two screens agree.
    """
    out: list[Finding] = []
    try:
        from locators.review import review_elements
        from nlp.fields import TARGET_IS_LOCATOR
        from nlp.parser import parse_step
        by_name: dict[str, list[dict]] = {}
        for f in review_elements(platform, with_usage=False):
            by_name.setdefault(f["name"], []).append(f)
    except Exception:  # noqa: BLE001
        return out
    seen: set[str] = set()
    for i, step in enumerate(steps, 1):
        try:
            cmd = parse_step(step)
        except Exception:  # noqa: BLE001
            continue
        names = []
        if cmd.type in TARGET_IS_LOCATOR and isinstance(cmd.target, str):
            names.append(cmd.target)
        if cmd.type == "swipe_until_visible":
            names += [v for v in (cmd.values or [])[1:] if isinstance(v, str)]
        for name in names:
            if name in seen or name not in by_name:
                continue
            seen.add(name)
            for f in by_name[name]:
                if f["kind"] in ("junk_name", "bad_name", "duplicate_name", "blank_selector",
                                 "fragile_selector"):
                    out.append(Finding(
                        kind=f"element_{f['kind']}",
                        severity=f["severity"], step_index=i,
                        message=f["message"], why=f["why"],
                        bucket="must" if f["severity"] == "high" else "can",
                        fix="", extra=[]))
                    break          # one line per element is enough here
    return out


# ── senior-reviewer checks ────────────────────────────────────────────────────
#
# The checks above find things that are wrong. These find things that are
# RISKY, WASTEFUL or ignore what the project already has — the notes a
# experienced reviewer leaves that a linter never would.

def _race_after_navigation(steps: list[str], appium: bool = False) -> list[Finding]:
    """
    Acting on a page the instant it is asked for, with nothing in between.

    Where the next step names the element it acts on, the missing wait can be
    written out exactly — it is a wait for that same element — so the finding
    carries the step rather than describing it. Where it does not (a search by
    text, say), the finding still stands; there is simply nothing to offer, and
    inventing an element name would be worse than saying nothing.
    """
    out: list[Finding] = []
    for i in range(len(steps) - 1):
        if not re.match(r"^\s*(open|go to url|navigate)\b", steps[i], re.I):
            continue
        nxt = steps[i + 1]
        if re.match(r"^\s*(wait|verify)\b", nxt, re.I):
            continue
        if re.match(r"^\s*(click|tap|type|fill|search)\b", nxt, re.I):
            target = _target_of(nxt)
            out.append(Finding(
                kind="race_after_navigation", severity="medium", step_index=i + 2,
                message="Acts on the page immediately after opening it.",
                why="Nothing here waits for the page to be ready, so this passes on "
                    "a fast machine and fails on a slow one — the classic flaky "
                    "test. A visibility check costs nothing when the page is quick.",
                fix="",
                # Goes in AT the action's position, pushing the action down —
                # the wait has to happen before the thing it protects.
                add_step=_wait_for(target, appium) if target else "",
                add_at=(i + 2) if target else 0))
    return out


def _redundant_waits(steps: list[str]) -> list[Finding]:
    """A fixed sleep next to a real wait is time spent for no extra safety."""
    out: list[Finding] = []
    for i in range(len(steps) - 1):
        a, b = steps[i].strip().lower(), steps[i + 1].strip().lower()
        #: Which of the two is the fixed sleep — the one the advice is about.
        #: The finding used to point at the LATER step whichever way round the
        #: pair fell, so half of them named the condition-based wait as the
        #: thing to remove, which is the opposite of the advice given.
        fixed_at = 0
        if a.startswith("wait until") and re.match(r"^wait\s+\d", b):
            fixed_at = i + 2
        elif re.match(r"^wait\s+\d", a) and b.startswith("wait until"):
            fixed_at = i + 1
        if fixed_at:
            out.append(Finding(
                kind="redundant_wait", severity="low", step_index=fixed_at,
                message="A fixed wait sits next to a condition-based wait.",
                why="The condition already waits exactly as long as needed. The "
                    "fixed one adds its full duration to every run and buys "
                    "nothing.", fix="", remove=True))
        if re.match(r"^wait\s+\d", a) and re.match(r"^wait\s+\d", b):
            out.append(Finding(
                kind="redundant_wait", severity="low", step_index=i + 2,
                message="Two fixed waits in a row.",
                why="Almost certainly one was added while debugging and never "
                    "removed. Combine them, or replace both with a wait for the "
                    "thing you actually need.", fix=""))
    return out


def _code_in_steps(steps: list[str]) -> list[Finding]:
    """
    JavaScript typed into a step.

    A test case is read by testers, not only by the runner. A step such as
    `store javascript "(function(){…getBoundingClientRect()…})()"` cannot be
    reviewed, edited from the form, or reused; the same check written as
    `verify element icon is inside every photo` can. Rule (29-Sep-2026): steps
    are plain language with named elements — code is a last resort, and a
    finding every time it appears.
    """
    out: list[Finding] = []
    for i, step in enumerate(steps, 1):
        if not re.match(r"^\s*(store|run)\s+javascript\b", step, re.I):
            continue
        out.append(Finding(
            kind="code_in_step", severity="high", step_index=i,
            message="This step is JavaScript, not a plain step.",
            why="Nobody can read or edit it without knowing the page's code, and "
                "the runner cannot heal it. Say what is being checked instead: "
                "'verify element X is visible', 'verify element icon is inside "
                "every photo', 'store position of icon in photo as p', 'store text "
                "of X as t', 'store count of X as n'. If no step exists for it, ask "
                "for the step to be added rather than writing code here.", fix=""))
    return out


def _raw_selectors(steps: list[str]) -> list[Finding]:
    """An XPath typed into a step instead of a named element."""
    out: list[Finding] = []
    for i, step in enumerate(steps, 1):
        # A URL's "https://" is not an XPath — strip URLs before looking.
        bare = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "", step, flags=re.I)
        if re.search(r"(//|css=|xpath=)[^\s\"]+", bare):
            out.append(Finding(
                kind="raw_selector", severity="medium", step_index=i,
                message="This step contains a selector rather than an element name.",
                why="A selector written inline is invisible to the element list, "
                    "so nothing can heal it when the page changes and no other "
                    "test can reuse it. Save it as a named element and the "
                    "self-healer starts covering it.", fix=""))
    return out


def _assertion_placement(steps: list[str]) -> list[Finding]:
    """A check that runs before the thing it is checking has been caused."""
    out: list[Finding] = []
    for i, step in enumerate(steps, 1):
        if not re.match(r"^\s*verify\b", step, re.I):
            continue
        earlier = steps[:i - 1]
        if not any(re.match(r"^\s*(click|tap|type|fill|search|open|go to)\b", e, re.I)
                   for e in earlier):
            out.append(Finding(
                kind="assertion_placement", severity="medium", step_index=i,
                message="Checks something before the test has done anything.",
                why="Nothing has happened yet, so this either passes trivially or "
                    "fails for the wrong reason. An assertion is only meaningful "
                    "after the action it is meant to prove.", fix=""))
            break
    return out


def _shadowed_elements(steps: list[str], platform: str) -> list[Finding]:
    """
    An element whose name is defined in more than one place.

    The runner takes the first match, which may not be the one the author had in
    mind — a quiet way for a test to click the right-looking wrong thing.
    """
    out: list[Finding] = []
    try:
        from locators.sources import conflicts

        clashes = conflicts(platform)
    except Exception:  # noqa: BLE001
        return out
    if not clashes:
        return out
    for i, step in enumerate(steps, 1):
        for name, entries in clashes.items():
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", step):
                winner = entries[0]
                others = ", ".join(f"{e.source_id}:{e.group}" for e in entries[1:])
                out.append(Finding(
                    kind="shadowed_element", severity="medium", step_index=i,
                    message=f"'{name}' is defined in more than one place.",
                    why=f"The runner resolves it to {winner.source_id}:{winner.group}; "
                        f"it is also defined in {others}. If that is not the one you "
                        f"meant, this step clicks the wrong element and still passes.",
                    fix=""))
                return out
    return out


def _length_and_shape(steps: list[str]) -> list[Finding]:
    """A test long enough that a failure no longer tells you much."""
    if len(steps) < 25:
        return []
    return [Finding(
        kind="long_test", severity="low", step_index=0,
        message=f"This test is {len(steps)} steps long.",
        why="When it fails you learn that something in a long journey broke, not "
            "what. Splitting it, or lifting the shared opening into a step group, "
            "makes each failure point at one thing.", fix="")]


def as_dicts(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]


def _block_structure(steps: list[str]) -> list[Finding]:
    """An if / loop that is not closed, or an 'else' / 'stop loop' in the wrong place."""
    from execution.control_flow import FlowProgram, FlowStructureError
    try:
        FlowProgram(steps)
    except FlowStructureError as e:
        m = re.match(r"Line (\d+):\s*(.*)", str(e))
        return [Finding(kind="block_structure", severity="high",
                        step_index=int(m.group(1)) if m else 0,
                        message=(m.group(2) if m else str(e)),
                        why="The test cannot start until every block is closed: "
                            "'if' → 'end if', 'for each row' → 'end for', "
                            "'repeat' → 'end repeat'.", fix="")]
    return []
