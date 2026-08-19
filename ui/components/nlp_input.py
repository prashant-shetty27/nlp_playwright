"""
ui/components/nlp_input.py — NLP Step Autocomplete Input

A smart text input that suggests NLP keywords and element names as the user types.
Core UX component for writing flow steps without memorising syntax.

Autocomplete sources (merged, ordered by relevance):
    1. Step TEMPLATES from nlp/keywords.py via POST /nlp/suggest — filtered to the
       selected platform, so a mobile-only command is never offered on web.
    2. Element names from the locator repository (GET /locators/dropdown/names),
       scoped to the platform — an element recorded only for Android is not a
       usable suggestion in a Website test.
    3. ${variable} tokens already referenced elsewhere in the flow.

Behaviour:
    - Suggestions appear after 1 character
    - Templates first, then locators, then variables
    - Enter accepts the highlighted suggestion; Enter on free text submits the step
    - Esc dismisses

Divergence from the original spec, deliberate:

    The spec listed suggestions as PHRASES ("click", "verify text"), which leaves
    the operator to remember the rest of the syntax. Each suggestion is now the
    complete `template` the API returns — `verify element {locator} is visible` —
    with the slots marked, so accepting one yields a step that parses.

    The spec's platform values were web|android|ios. Platform is now the canonical
    vocabulary (website|mobilesite|android|ios|hybrid), which is what decides
    which commands are dispatchable.

Props:
    platform:       str — canonical platform name
    on_submit:      callable(nlp_text: str)
    placeholder:    str
    initial_value:  str
    known_variables: list[str] — offered as ${...} completions

Used in: pages/platform/*/test_cases.py
"""
from __future__ import annotations

import re
from typing import Callable

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY

_SLOT = re.compile(r"\{(\w+)\}")


class NlpInput:
    """An input that proposes complete, parseable steps rather than fragments."""

    def __init__(self, platform: str, on_submit: Callable[[str], None], *,
                 placeholder: str = "Type a step, e.g. 'click ask_more_photos_cta'",
                 initial_value: str = "",
                 known_variables: list[str] | None = None) -> None:
        self.platform = platform
        self.on_submit = on_submit
        self.known_variables = known_variables or []
        self._locators: list[str] = []
        self._labels: dict[str, str] = {}
        #: Index of the highlighted suggestion. -1 means "none", so Enter submits
        #: the typed text rather than silently accepting a row the operator never
        #: looked at.
        self._active = -1
        self._rows: list[tuple[str, str, str]] = []

        with ui.column().classes("w-full gap-1"):
            self.input = (
                ui.input(placeholder=placeholder, value=initial_value)
                .props("outlined dense clearable autocomplete=off")
                .classes("w-full")
                .style(f"font-family:{TYPOGRAPHY['mono']}")
            )
            # Enter accepts the highlighted row if there is one, otherwise it
            # submits what was typed. Arrow keys move the highlight — without
            # this the list could only be used with the mouse, which is the
            # wrong hand for something you reach mid-sentence.
            self.input.on("keydown.enter", self._on_enter)
            self.input.on("keydown.down", lambda _: self._move(1))
            self.input.on("keydown.up", lambda _: self._move(-1))
            self.input.on("keydown.tab", lambda _: self._accept_active())
            self.input.on("keydown.escape", lambda _: self._clear())
            self.input.on_value_change(lambda _: self._refresh())

            self.hint = ui.label().style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                f"font-family:{TYPOGRAPHY['family']}; min-height:1.1rem"
            )
            self.panel = ui.column().classes("w-full gap-0").style(
                f"border:1px solid {COLORS['border']}; border-radius:6px;"
                f"max-height:16rem; overflow-y:auto; display:none"
            )

    async def load(self) -> None:
        """Fetch the locator list once; suggestions are fetched per keystroke."""
        try:
            # Scoped to the platform, like the step templates above it. Offering
            # an element the runner cannot resolve is offering a step that will
            # fail — and it is offered at the exact moment the author is least
            # able to tell, because they are picking the name from a list.
            self._labels = await api.locator_labels(self.platform)
            self._locators = sorted(self._labels)
        except api.ApiError as e:
            self.hint.set_text(f"Locator list unavailable: {e.detail[:60]}")

    def _clear(self) -> None:
        self.panel.clear()
        self.panel.style("display:none")
        self._active = -1
        self._rows = []

    def _move(self, delta: int) -> None:
        """Move the highlight, wrapping at both ends."""
        if not self._rows:
            return
        self._active = (self._active + delta) % len(self._rows)
        self._paint()

    def _on_enter(self, _=None) -> None:
        if self._active >= 0 and self._active < len(self._rows):
            self._accept_active()
        else:
            self._submit()

    def _accept_active(self, _=None) -> None:
        if 0 <= self._active < len(self._rows):
            row = self._rows[self._active]
            self._accept(row["insert"], row["kind"])

    def _submit(self, _=None) -> None:
        text = (self.input.value or "").strip()
        if not text:
            return
        self._clear()
        self.on_submit(text)
        self.input.set_value("")
        self.hint.set_text("")

    async def _refresh(self) -> None:
        partial = (self.input.value or "").strip()
        if len(partial) < 1:
            self._clear()
            self.hint.set_text("")
            return

        # A step is validated as typed, so the operator learns immediately rather
        # than at generation time. Failing to parse is normal mid-typing, so it is
        # phrased as a hint, never an error.
        try:
            parsed = await api.parse_step(partial)
            self.hint.set_text(f"✓ parses as {parsed.get('type')}")
            self.hint.style(f"color:{COLORS['success']}")
        except api.ApiError:
            self.hint.set_text("keep typing…")
            self.hint.style(f"color:{COLORS['text_muted']}")

        rows: list[dict] = []
        try:
            for s in await api.suggest(partial, self.platform, limit=8):
                tmpl = s.get("template") or s.get("phrase", "")
                rows.append({"insert": tmpl, "display": tmpl,
                             "kind": s.get("action", ""),
                             # A step group is not a command — it stands for
                             # several steps — so it has to LOOK different in the
                             # list. Without the tag and the preview it reads as
                             # an ordinary one-line action.
                             "tag": s.get("tag", ""),
                             "detail": s.get("detail", ""),
                             "steps": s.get("steps", [])})
        except api.ApiError:
            pass

        low = partial.lower()
        word = low.split()[-1] if low.split() else low
        for name in self._locators:
            if word and word in name and len(rows) < 20:
                rows.append({"insert": name, "kind": "locator",
                             "display": name, "detail": self._labels.get(name, ""),
                             "tag": "", "steps": []})
        for var in self.known_variables:
            if word and word in var.lower() and len(rows) < 24:
                rows.append({"insert": "${" + var + "}", "kind": "variable",
                             "display": "${" + var + "}", "detail": "",
                             "tag": "", "steps": []})

        self._rows = rows[:24]
        # Nothing is highlighted until an arrow key says so. Pre-highlighting the
        # first row meant Enter accepted a suggestion the operator had never
        # looked at — typing a complete step and pressing Enter replaced it with
        # whatever happened to match, and the step was silently lost.
        self._active = -1
        self._paint()

    def _paint(self) -> None:
        self.panel.clear()
        if not self._rows:
            self.panel.style("display:none")
            return
        self.panel.style("display:block")
        with self.panel:
            for i, row in enumerate(self._rows):
                self._row(row, active=(i == self._active))

    def _accept(self, insert: str, kind: str) -> None:
        """
        Choosing a suggestion IS the action — clicking or Entering commits it.

        It used to only fill the box, so every suggestion cost two gestures: pick
        it, then press Enter again to actually add the step. Now a choice that
        leaves nothing to fill in is submitted straight away; one that still has
        a {slot} stays in the box, because there is genuinely more to say.
        """
        if kind in ("locator", "variable"):
            parts = (self.input.value or "").split()
            parts[-1:] = [insert]
            text = " ".join(parts)
        else:
            text = insert
        self.input.set_value(text)
        self._clear()

        if _SLOT.search(text):
            self.hint.set_text("fill the highlighted value, then press Enter")
            self.hint.style(f"color:{COLORS['warning']}")
            return
        # Only commit something the parser accepts. Auto-submitting a fragment
        # would put an unrunnable line into the test on a single click.
        ui.timer(0.01, lambda: self._submit_if_valid(text), once=True)

    async def _submit_if_valid(self, text: str) -> None:
        try:
            await api.parse_step(text)
        except api.ApiError:
            self.hint.set_text("keep typing…")
            self.hint.style(f"color:{COLORS['text_muted']}")
            return
        self._submit()

    def _row(self, row: dict, active: bool = False) -> None:
        insert, kind = row["insert"], row["kind"]
        is_group = row.get("tag") == "sg"
        shown = {"open": False}

        def accept() -> None:
            self._accept(insert, kind)

        holder = ui.column().classes("w-full gap-0").style(
            "border-bottom:1px solid #F1F5F9;"
            + (f"background:{COLORS['primary']}14" if active else ""))
        with holder:
            with ui.row().classes("w-full items-center gap-2 px-2 py-1"):
                if is_group:
                    # Expand in place: you should be able to see WHAT a group
                    # does before committing three steps you cannot see.
                    def toggle() -> None:
                        shown["open"] = not shown["open"]
                        preview.set_visibility(shown["open"])
                        caret.props(f"icon={'expand_less' if shown['open'] else 'expand_more'}")

                    caret = ui.button(icon="expand_more", on_click=toggle) \
                        .props("flat dense size=xs").tooltip("Show the steps inside")
                    ui.label("sg").style(
                        f"background:{COLORS['primary']}22; color:{COLORS['primary']};"
                        f"border-radius:4px; padding:0 6px;"
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"font-family:{TYPOGRAPHY['mono']}; font-weight:600")
                else:
                    ui.label({"locator": "◆", "variable": "$"}.get(kind, "▸")).style(
                        f"color:{COLORS['text_muted']}; width:1rem")

                lbl = ui.label(row["display"]).classes("cursor-pointer").style(
                    f"font-family:{TYPOGRAPHY['mono']};"
                    f"font-size:{TYPOGRAPHY['size_sm']}")
                lbl.on("click", accept)

                if row.get("detail"):
                    ui.label(row["detail"]).classes("ml-auto").style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}")
                elif kind not in ("locator", "variable") and _SLOT.search(insert):
                    ui.label(f"fill: {', '.join(_SLOT.findall(insert))}") \
                        .classes("ml-auto").style(
                            f"font-size:{TYPOGRAPHY['size_xs']};"
                            f"color:{COLORS['warning']}")

            preview = ui.column().classes("w-full gap-0").style(
                f"padding:0 0 4px 2.6rem; border-left:2px solid {COLORS['primary']}33")
            with preview:
                for j, st in enumerate(row.get("steps", []), 1):
                    ui.label(f"{j}. {st}").style(
                        f"font-family:{TYPOGRAPHY['mono']};"
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}")
            preview.set_visibility(False)


def nlp_input(platform: str, on_submit: Callable[[str], None], **kw) -> NlpInput:
    return NlpInput(platform, on_submit, **kw)
