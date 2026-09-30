"""
ai_flow_builder/mapper.py

Maps a manual Testsigma-NLP step onto a codeless statement this framework can
actually execute. Every mapping is checked against the live catalogue, so the
mapper can only ever emit commands the runtime really dispatches.

A step is never silently dropped. Each one gets a status:

  SUPPORTED              direct 1:1 command exists
  SUPPORTED_VIA_SUBSTITUTE  no direct command; a documented equivalent is used
  NEEDS_LOCATOR          command exists, referenced element is not recorded
  UNSUPPORTED_ACTION     no command and no honest substitute — no statement emitted
  NEEDS_CLARIFICATION    the step could not be understood
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ai_flow_builder.bundle import referenced_variables
from ai_flow_builder.catalogue import Catalogue

SUPPORTED = "SUPPORTED"
SUBSTITUTE = "SUPPORTED_VIA_SUBSTITUTE"
NEEDS_LOCATOR = "NEEDS_LOCATOR"
UNSUPPORTED = "UNSUPPORTED_ACTION"
NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"


def to_locator_name(element: str) -> str:
    """Testsigma CamelCase element name → framework snake_case locator name."""
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", element.strip())
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")


@dataclass
class StepMapping:
    source_ref: str = ""          # e.g. "TC_AFP_C01 row 84"
    manual_step: str = ""
    intent: str = ""
    command_type: str = ""
    statement: str = ""
    status: str = NEEDS_CLARIFICATION
    locator: str = ""
    locator_reused: bool = False
    variables: list[str] = field(default_factory=list)
    evidence: str = ""
    note: str = ""

    @property
    def emits(self) -> bool:
        return bool(self.statement) and self.status in (SUPPORTED, SUBSTITUTE, NEEDS_LOCATOR)


# ─────────────────────────────────────────────────────────────────────────────
# Rules — ordered; first match wins. Each returns a partially-filled StepMapping.
# ─────────────────────────────────────────────────────────────────────────────

_RULES: list[tuple[str, re.Pattern]] = [
    ("navigate",      re.compile(r"^\s*(?:navigate\s+to|open|go\s+to)\s+(?P<target>\S+)\s*$", re.I)),
    ("enter",         re.compile(r"^\s*(?:enter|type)\s+(?P<value>.+?)\s+in(?:to)?\s+element\s+(?P<el>\w+)\s*$", re.I)),
    ("click",         re.compile(r"^\s*(?:click|tap)\s+on\s+element\s+(?P<el>\w+)\s*$", re.I)),
    ("verify_text",   re.compile(r"^\s*verify\s+that\s+the\s+text\s+of\s+element\s+(?P<el>\w+)\s+is\s+(?P<text>.+?)\s*$", re.I)),
    ("wait_visible",  re.compile(r"^\s*wait\s+(?:until|for)\s+element\s+(?P<el>\w+)\s+is\s+displayed\s*$", re.I)),
    ("verify_visible", re.compile(r"^\s*verify\s+that\s+element\s+(?P<el>\w+)\s+is\s+displayed\s*$", re.I)),
    # Both negative wordings the workbook uses. Which assertion this becomes is
    # decided in the handler from the wording itself — "in DOM"/"present"/"exist"
    # means absence from the document, "displayed"/"visible" means merely unseen.
    ("verify_not_visible", re.compile(
        r"^\s*verify\s+that\s+element\s+(?P<el>\w+)\s+"
        r"(?:is\s+not\s+(?:displayed|visible|present(?:\s+in\s+(?:the\s+)?dom)?)"
        r"|is\s+absent|does\s+not\s+(?:exist|appear))\s*$", re.I)),
    ("store_count",   re.compile(r"^\s*store\s+the\s+count\s+of\s+elements?\s+(?P<el>\w+)(?:\s+in\s+variable\s+(?P<var>\w+))?\s*$", re.I)),
    ("clear_cookies", re.compile(r"^\s*clear\s+(?:browser\s+)?cookies\s*$", re.I)),
    ("swipe",         re.compile(r"^\s*swipe\s+(?P<dir>left|right)\s+on\s+element\s+(?P<el>\w+)\s*$", re.I)),
    ("wait_seconds",  re.compile(r"^\s*wait\s+(?P<n>\d+)\s*(?:seconds?|s)\s*$", re.I)),
]


class Mapper:
    def __init__(self, catalogue: Catalogue, variable_bindings: dict[str, str] | None = None):
        self.cat = catalogue
        self.bindings = variable_bindings or {}
        self.required_locators: dict[str, str] = {}   # locator_name → source element

    # ── helpers ──────────────────────────────────────────────────────────────
    def _bind(self, text: str) -> tuple[str, list[str]]:
        """Rewrite source variables onto the approved secure placeholders."""
        used = sorted(referenced_variables(text))
        out = text
        for src in used:
            target = self.bindings.get(src)
            if target:
                out = out.replace("${" + src + "}", target)
        return out, [self.bindings.get(u, "${" + u + "}") for u in used]

    def _locator(self, element: str) -> tuple[str, bool]:
        name = to_locator_name(element)
        reused = self.cat.has_locator(name)
        self.required_locators.setdefault(name, element)
        return name, reused

    def _check(self, m: StepMapping) -> StepMapping:
        """Gate a candidate mapping against the live catalogue."""
        if m.command_type and not self.cat.supports(m.command_type):
            m.status, m.statement = UNSUPPORTED, ""
            m.evidence = self.cat.evidence_for(m.command_type)
            return m
        if m.command_type:
            m.evidence = self.cat.evidence_for(m.command_type)
        if m.locator and not m.locator_reused and m.status == SUPPORTED:
            m.status = NEEDS_LOCATOR
        return m

    # ── main entry ───────────────────────────────────────────────────────────
    def map_step(self, manual: str, source_ref: str) -> StepMapping:
        native = self._native(manual, source_ref)
        if native is not None:
            return native
        for kind, rx in _RULES:
            mo = rx.match(manual)
            if mo:
                return self._check(getattr(self, f"_r_{kind}")(mo, manual, source_ref))
        return StepMapping(source_ref=source_ref, manual_step=manual,
                           intent="could not be parsed into a known step shape",
                           status=NEEDS_CLARIFICATION)

    # ── per-rule handlers ────────────────────────────────────────────────────
    def _native(self, manual: str, ref: str) -> StepMapping | None:
        """
        A step already written in the framework's own grammar passes through.

        The drafter now writes that grammar directly (it is shown the live
        vocabulary), so most steps arrive executable. Translating them through
        the legacy manual shapes would only lose information — and flag every
        `api get` / `store json` / `store regex` as "could not be parsed".
        A `# comment` line is kept as-is too: it is how a generated flow
        explains itself to whoever debugs it.
        """
        text = (manual or "").strip()
        if not text:
            return None
        if text.startswith("#"):
            return StepMapping(ref, manual, "explanatory comment", "comment", text, SUPPORTED)
        try:
            from nlp.fields import TARGET_IS_LOCATOR
            from nlp.parser import parse_step

            probe = re.sub(r"\$\{[^}]+\}", "VAR", text)
            cmd = parse_step(probe)
        except Exception:  # noqa: BLE001 — not native grammar; try the legacy shapes
            return None
        ctype = getattr(cmd, "type", "") or ""
        if ctype == "block":                  # if / else / loops: decided by the runner itself
            return StepMapping(ref, manual, "block line", "block", text, SUPPORTED)
        if ctype and not self.cat.supports(ctype):
            return None                       # let the legacy rules give an honest verdict
        statement, vs = self._bind(text)
        loc, reused = "", False
        if ctype in TARGET_IS_LOCATOR and getattr(cmd, "target", None):
            name = str(cmd.target).strip()
            loc = to_locator_name(name)
            reused = self.cat.has_locator(loc)
            self.required_locators.setdefault(loc, name)
            if loc != name:
                statement = statement.replace(name, loc, 1)
        m = StepMapping(ref, manual, f"native step ({ctype})", ctype, statement,
                        SUPPORTED, loc, reused, vs)
        return self._check(m)

    def _r_navigate(self, mo, manual, ref):
        target, vs = self._bind(mo.group("target"))
        return StepMapping(ref, manual, "Load the page under test", "open",
                           f"open {target}", SUPPORTED, variables=vs)

    #: Locators that are multi-input OTP collections — a single fill would be invalid.
    OTP_LOCATORS = {"otp_input"}

    def _r_enter(self, mo, manual, ref):
        value, vs = self._bind(mo.group("value").strip())
        loc, reused = self._locator(mo.group("el"))
        if loc in self.OTP_LOCATORS and self.cat.supports("enter_otp"):
            return StepMapping(ref, manual,
                               f"Distribute the OTP across the {mo.group('el')} inputs",
                               "enter_otp", f'enter otp "{value}" into {loc}',
                               SUPPORTED, loc, reused, vs,
                               note="multi-input OTP field — a single fill would be invalid")
        return StepMapping(ref, manual, f"Type a value into {mo.group('el')}", "fill",
                           f'type "{value}" into {loc}', SUPPORTED, loc, reused, vs)

    def _r_click(self, mo, manual, ref):
        loc, reused = self._locator(mo.group("el"))
        return StepMapping(ref, manual, f"Click {mo.group('el')}", "click",
                           f"click {loc}", SUPPORTED, loc, reused)

    def _r_verify_text(self, mo, manual, ref):
        text, vs = self._bind(mo.group("text").strip().strip('"'))
        loc, reused = self._locator(mo.group("el"))
        return StepMapping(ref, manual, f"Assert exact text of {mo.group('el')}",
                           "verify_element_exact",
                           f'verify element {loc} has text "{text}"',
                           SUPPORTED, loc, reused, vs)

    def _r_wait_visible(self, mo, manual, ref):
        loc, reused = self._locator(mo.group("el"))
        # Web has no wait_for_element (Appium-only). store text auto-waits on the
        # locator and raises when absent, which is the honest web equivalent.
        if self.cat.supports("wait_until_visible"):
            return StepMapping(ref, manual, f"Wait for {mo.group('el')} to become visible",
                               "wait_until_visible",
                               f"wait until element {loc} is visible", SUPPORTED, loc, reused)
        if self.cat.supports("wait_for_element"):
            return StepMapping(ref, manual, f"Wait for {mo.group('el')}", "wait_for_element",
                               f"wait for element {loc}", SUPPORTED, loc, reused)
        return StepMapping(ref, manual, f"Wait for {mo.group('el')}", "wait_for_element",
                           "", UNSUPPORTED, loc, reused,
                           note="no condition-based wait available on this platform")

    def _r_verify_visible(self, mo, manual, ref):
        loc, reused = self._locator(mo.group("el"))
        if self.cat.supports("verify_element_visible"):
            return StepMapping(ref, manual, f"Assert {mo.group('el')} is visible",
                               "verify_element_visible",
                               f"verify element {loc} is visible", SUPPORTED, loc, reused)
        if self.cat.supports("verify_element_exists"):
            return StepMapping(ref, manual, f"Assert {mo.group('el')} present",
                               "verify_element_exists", f"verify element exists {loc}",
                               SUPPORTED, loc, reused)
        return StepMapping(ref, manual, f"Assert {mo.group('el')} present",
                           "verify_element_exists", "", UNSUPPORTED, loc, reused,
                           note="no visibility assertion available on this platform")

    def _r_verify_not_visible(self, mo, manual, ref):
        """
        'not present in DOM' and 'not displayed' are different claims, and the
        source wording says which one is meant. DOM absence is the stronger
        assertion, so it is only used when the step actually asks for it.
        """
        loc, reused = self._locator(mo.group("el"))
        wants_dom = bool(re.search(r"\b(dom|present|exist)\b", manual, re.I))
        cmd = "verify_element_not_exists" if wants_dom else "verify_element_not_visible"
        phrase = "is not present" if wants_dom else "is not visible"
        if self.cat.supports(cmd):
            return StepMapping(ref, manual, f"Assert {mo.group('el')} absent", cmd,
                               f"verify element {loc} {phrase}", SUPPORTED, loc, reused)
        return StepMapping(ref, manual, f"Assert {mo.group('el')} absent", cmd, "",
                           UNSUPPORTED, loc, reused,
                           note=f"'{cmd}' is not dispatchable on this platform")

    def _r_store_count(self, mo, manual, ref):
        loc, reused = self._locator(mo.group("el"))
        var = mo.group("var") or f"{loc}_count"
        return StepMapping(ref, manual, f"Count {mo.group('el')}", "extract_count",
                           f"store count of {loc} as {var}", SUPPORTED, loc, reused)

    def _r_clear_cookies(self, mo, manual, ref):
        return StepMapping(ref, manual, "Start from a clean, logged-out session", "",
                           "", UNSUPPORTED,
                           note="no cookie command exists; satisfied structurally — "
                                "open_browser() creates a fresh context per run, so the "
                                "session is already clean at flow start")

    def _r_swipe(self, mo, manual, ref):
        loc, reused = self._locator(mo.group("el"))
        d = mo.group("dir").lower()
        return StepMapping(ref, manual, f"Swipe {d}", f"swipe_{d}",
                           f"swipe {d}", SUPPORTED, loc, reused)

    def _r_wait_seconds(self, mo, manual, ref):
        n = mo.group("n")
        return StepMapping(ref, manual, f"Pause {n}s", "wait",
                           f"wait {n} seconds", SUPPORTED)


def summarise(mappings: list[StepMapping]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in mappings:
        out[m.status] = out.get(m.status, 0) + 1
    return dict(sorted(out.items()))
