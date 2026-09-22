"""
ui/components/token_step.py — a step whose values are individually clickable.

The problem this solves
-----------------------
A step used to render as one uniform line of monospace text, identical in weight
to the box you type into. Two consequences, both reported from real use:

  * You could not tell stored content from an input field at a glance.
  * Changing one element meant clicking the pencil and retyping the whole line,
    even though the only thing you wanted to change was the element name.

So the values inside a step are their own controls. `click maybe_later_link`
draws "click" as plain text and `maybe_later_link` as a link. Clicking the link
replaces JUST that token with an input — the rest of the step stays put — and
offers what fits that token: saved elements for an element, nothing but digits
for a count, known variables for a variable.

An unfilled template slot works the same way: insert `wait {number} seconds` and
`{number}` is already a link, marked as needing a value.

The pencil still exists, and still means what it says: edit the ENTIRE line.

Unknown elements
----------------
Typing an element that is not in the database is the normal way to discover you
need to record one. Rather than accepting it silently — which produces a step
that lints clean until it runs — the editor offers to save it, and asks for the
selector and the group. The name is normalised on save, so what gets stored
follows the same convention as everything else.
"""
from __future__ import annotations

import asyncio
import re
from typing import Callable

from nicegui import ui

from ui import api_client as api
from ui.theme import COLORS, TYPOGRAPHY

#: A whole token that is nothing but a ${reference}.
_REFERENCE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")

#: What each role means when its token is clicked.
ROLE_HINT = {
    "locator": "an element from your saved list",
    "variable": "a runtime variable",
    "number": "a number",
    "text": "any text",
    "url": "a URL",
    "file": "a data file path",
}


class TokenStep:
    """Renders one step, with each editable value as its own control."""

    def __init__(self, step: str, *, platform: str,
                 on_change: Callable[[str], None],
                 locators: dict[str, str] | None = None,
                 locator_details: dict[str, dict[str, str]] | None = None,
                 locators_elsewhere: dict[str, str] | None = None,
                 variables: list[str] | None = None,
                 known_values: set[str] | None = None) -> None:
        self.step = step
        self.platform = platform
        self.on_change = on_change
        #: Elements THIS platform's runner resolves. Anything outside this set
        #: will fail at run time, whether or not it exists somewhere else.
        self.locators = locators or {}
        #: Enough detail to let one step fork a saved locator into its own copy.
        self.locator_details = locator_details or {}
        #: Elements recorded against another platform. Not usable here, but
        #: worth naming: "not in your element list" sends the author off to
        #: record a duplicate of something they already have.
        self.locators_elsewhere = locators_elsewhere or {}
        #: ${variables} that already have a value by the time this step runs —
        #: set by an earlier step, or held in Test Data. A reference to anything
        #: else is a value the operator will be asked for, which is exactly the
        #: state amber is for.
        self.known_values = known_values or set()
        self.container = ui.row().classes("items-center gap-0 flex-wrap") \
            .style("min-height:1.6rem")

    async def render(self) -> None:
        """Draw the step. Segmentation comes from the API so the UI owns no grammar."""
        self.container.clear()
        try:
            info = await api.segment_step(self.step)
            segments = info.get("segments", [])
        except api.ApiError:
            segments = [{"text": self.step, "kind": "fixed"}]

        with self.container:
            for seg in segments:
                if seg.get("kind") == "fixed":
                    self._fixed(seg.get("text", ""))
                else:
                    self._token(seg)

    # ── pieces ────────────────────────────────────────────────────────────────

    def _fixed(self, text: str) -> None:
        if not text:
            return
        ui.label(text).style(
            f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']};"
            f"color:{COLORS['text']}; white-space:pre")

    def _token(self, seg: dict) -> None:
        """One editable value, drawn as a link until it is clicked."""
        unfilled = seg.get("kind") == "slot"
        role = seg.get("role") or "text"
        value = (seg.get("text") or "").strip()

        # Amber means "this will not run yet". That covers an unfilled {slot} AND
        # an element name this platform's runner cannot resolve — both are the
        # same problem to the person reading the step, and colouring the second
        # one blue like a working element hid the very thing they need to fix.
        #
        # "Cannot resolve HERE" is the test, not "does not exist anywhere". The
        # element list used to be read across every platform at once, so an
        # Android-only element drew blue in a Website test while the review
        # panel — which is platform-scoped — called the same name unknown.
        missing_element = (role == "locator" and value
                           and value not in self.locators)
        other_platform = missing_element and value in self.locators_elsewhere
        # A ${reference} with nothing behind it is the same kind of gap, whatever
        # the role happens to be — `open ${referral_url}` is a url, `type
        # "${mobile}"` is text, and both are a value somebody still has to
        # supply. Colouring them blue said "this is filled in" about a step that
        # cannot run until it is.
        ref = _REFERENCE.fullmatch(value)
        unset_variable = bool(ref) and ref.group(1) not in self.known_values
        needs_attention = unfilled or missing_element or unset_variable
        colour = COLORS["warning"] if needs_attention else COLORS["primary"]

        holder = ui.element("span")
        # Everything belonging to one token — the `v` tag, the name, the caret
        # that opens its menu — has to sit on one line. As plain block children
        # of the span they stacked instead, so the caret hung below every
        # element name and the tag above every variable, giving each row a
        # ragged second line of stray marks.
        # white-space:normal + min-width:0 on the value (below) is what lets a
        # very long token — a 300-character Justdial URL — break across lines.
        # With nowrap the token could not shrink, the row grew past the page,
        # and the Save / Delete / Run toolbar and every row's edit buttons were
        # pushed off-screen to the right: the mobilesite test looked like it had
        # no buttons at all.
        holder.style("display:inline-flex; align-items:center; white-space:normal;"
                     " min-width:0; max-width:100%")
        with holder:
            # A variable is marked as one, the way a step group is marked `sg`
            # in the suggestion list. Without it `${otp}` and a quoted literal
            # are two underlined tokens that look alike, and which one carries a
            # value that changes per run is the thing you most need to see.
            # It takes the token's own colour, so an unset variable is amber
            # tag and all rather than an amber word with a blue badge on it.
            if ref:
                ui.label("v").style(
                    f"background:{colour}22; color:{colour}; border-radius:3px;"
                    f"padding:0 4px; margin-right:3px;"
                    f"font-size:{TYPOGRAPHY['size_xs']};"
                    f"font-family:{TYPOGRAPHY['mono']}; font-weight:600") \
                    .tooltip("a variable — its value is supplied at run time")
            chip = ui.label(seg.get("text", "")).style(
                f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']};"
                f"color:{colour}; cursor:pointer; text-decoration:underline;"
                f"text-decoration-style:{'dashed' if needs_attention else 'solid'};"
                f"text-underline-offset:3px; padding:0 2px; border-radius:3px;"
                f"background:{colour}14; word-break:break-all; min-width:0"
            )
            if other_platform:
                # Naming the platform it DOES belong to is the difference
                # between "record it again" and "you are on the wrong platform,
                # or this element needs one for this one too".
                hint = (f"'{value}' is recorded, but not for {self.platform} — "
                        f"{self.locators_elsewhere[value]}. This step will not "
                        f"run here. Click to point it somewhere else, or add it "
                        f"for {self.platform}.")
            elif missing_element:
                hint = f"'{value}' is not in your element list — click to add it"
            elif unset_variable:
                hint = (f"Nothing sets {value} yet. You will be asked for it in "
                        f"Run Center each time, or save it once under Test Data "
                        f"and every run picks it up.")
            else:
                hint = f"Click to change — {ROLE_HINT.get(role, 'a value')}"
            chip.tooltip(hint)
            chip.on("click", lambda: self._edit(holder, seg))

            # For an element there are two different intentions behind clicking
            # it: "point this step at a different element" and "the element
            # itself is wrong". The first belongs here; the second belongs on the
            # Elements screen, where the naming rules and the duplicate checks
            # live. A right-click / caret menu keeps them apart instead of making
            # one gesture guess.
            if role == "locator" and not unfilled and not missing_element:
                name = (seg.get("text") or "").strip()
                details = self.locator_details.get(name, {})
                with ui.menu().props("auto-close") as menu:
                    ui.menu_item("Point this step at another element",
                                 lambda: self._edit(holder, seg))
                    ui.menu_item(
                        f"Create a new locator from '{name}'",
                        lambda n=name, s=seg,
                        g=details.get("group", ""),
                        x=details.get("selector", ""):
                        self._create_locator_from(n, s, g, x),
                    )
                    ui.separator()
                    ui.menu_item(f"Edit '{name}' — selector or name",
                                 lambda n=name: self._open_in_elements(n))
                    ui.menu_item(f"Rename '{name}' everywhere",
                                 lambda n=name: self._open_in_elements(n))
                caret = ui.button(icon="expand_more").props("flat dense size=xs") \
                    .style("min-width:1rem; padding:0")
                caret.tooltip("More things you can do with this element")
                with caret:
                    menu

    def _edit(self, holder, seg: dict) -> None:
        """Swap this token for an input; everything else on the line stays as it is."""
        holder.clear()
        start, end = seg.get("start", 0), seg.get("end", 0)
        role = seg.get("role") or "text"
        current = "" if seg.get("kind") == "slot" else seg.get("text", "")
        #: What was there before this edit. Kept because a raw selector typed
        #: straight into a step is the one thing the add-element form cannot
        #: work out for itself, and it is sitting right here.
        was = current
        # A ${placeholder} is something still to be filled, not text to edit. It
        # was being loaded into the box verbatim, so typing a value left the "${"
        # behind — "${search_term}" became "${baldev engineering}" and the run
        # failed on a variable that was never defined. Treat it like an empty
        # slot: click it, it clears, you type the value.
        if current.startswith("${") and current.endswith("}"):
            self._placeholder_name = current[2:-1]
            current = ""

        with holder:
            col = ui.column().classes("gap-0").style("display:inline-block")
            with col:
                box = ui.input(value=current, placeholder=ROLE_HINT.get(role, "value")) \
                    .props("outlined dense autofocus") \
                    .style(f"font-family:{TYPOGRAPHY['mono']}; min-width:12rem")
                menu = ui.column().classes("gap-0").style(
                    f"position:absolute; z-index:50; background:{COLORS['surface']};"
                    f"border:1px solid {COLORS['border']}; border-radius:6px;"
                    f"max-height:12rem; overflow-y:auto; min-width:16rem; display:none")

            async def commit(_=None) -> None:
                value = (box.value or "").strip()
                if not value:
                    # An empty box means "leave it alone", not "blank it out" —
                    # so the token comes back exactly as it was.
                    await self.render()
                    return
                if role == "number" and not value.replace(".", "", 1).isdigit():
                    ui.notify("That field takes a number", type="warning")
                    return
                if value.startswith("${") and value.endswith("}"):
                    # Typed a reference deliberately — leave it as a reference.
                    self._apply(start, end, value)
                    return
                if role == "locator" and value not in self.locators:
                    # The step is passed in so the namer knows what the element
                    # is FOR — "click" and "type into" want different names for
                    # the same selector.
                    #
                    # `was` is what the token held BEFORE the name was typed
                    # over it. When that was a raw selector, it IS the selector
                    # being named, and the dialog opened with an empty "Paste it
                    # here" — so naming an XPath already written into the step
                    # meant finding it again and pasting it back in.
                    _unknown_locator_dialog(
                        value, self.platform,
                        on_saved=lambda saved: self._apply(start, end, saved),
                        on_use_anyway=lambda: self._apply(start, end, value),
                        step=self.step, selector_hint=was)
                    return
                self._apply(start, end, value)

            async def suggest(_=None) -> None:
                typed = (box.value or "").strip().lower()
                pool: list[tuple[str, str, str]] = []     # insert, name, note
                if role == "locator":
                    pool = [(n, n, self.locators.get(n, "")) for n in sorted(self.locators)
                            if typed in n.lower()][:12]
                elif role == "variable":
                    pool = [(v, v, "runtime variable") for v in self.variables
                            if typed in v.lower()][:12]

                # Saved test data is offered for any value-bearing field, matched
                # on the NAME or on the value itself — you remember the number,
                # not what it was called, so typing "93" finds the test mobile.
                # What gets inserted is the REFERENCE, never the digits: the step
                # reads ${test_mobile} and shows 93---210, so the number stays out
                # of the flow file, the report and the screenshot.
                if role in ("text", "number", "url", "variable"):
                    try:
                        for g in await api.testdata_suggest(typed, limit=8):
                            note = f"{g['display']}  ({g['tag']})"
                            if g.get("is_secret") and not g.get("defined"):
                                note = f"{g['display']} — set it in .env  ({g['tag']})"
                            pool.append((g["insert"], g["name"], note))
                    except api.ApiError:
                        pass
                menu.clear()
                if not pool:
                    menu.style("display:none")
                    return
                menu.style("display:block")
                with menu:
                    for insert, name, note in pool:
                        # Picking a value commits it. Setting the box and waiting
                        # for Enter made every choice a two-step action.
                        def pick(v=insert) -> None:
                            menu.style("display:none")
                            done["handled"] = True
                            self._apply(start, end, v)
                        with ui.row().classes("w-full items-center gap-2 cursor-pointer px-2 py-1") \
                                .style("border-bottom:1px solid #F1F5F9").on("click", pick):
                            ui.label(name).style(
                                f"font-family:{TYPOGRAPHY['mono']};"
                                f"font-size:{TYPOGRAPHY['size_sm']}")
                            ui.label(note).classes("ml-auto").style(
                                f"font-size:{TYPOGRAPHY['size_xs']};"
                                f"color:{COLORS['text_muted']}")

            #: Set by anything that has already decided what this token becomes,
            #: so a later cancel cannot undo it or redraw over it.
            done = {"handled": False}

            async def cancel(_=None) -> None:
                """Put the token back exactly as it was. Changes nothing."""
                if done["handled"]:
                    return
                done["handled"] = True
                await self.render()

            def commit_once(_=None):
                if done["handled"]:
                    return None
                done["handled"] = True
                return commit()

            async def blur(_=None) -> None:
                """
                Clicking away puts the token back, like Escape.
                
                Deferred by a beat because picking a suggestion ALSO blurs the
                box — cancelling immediately would tear the list down before
                the click on it landed, so the pick sets `handled` first and
                this then does nothing.
                """
                await asyncio.sleep(0.2)
                await cancel()

            box.on_value_change(suggest)
            box.on("keydown.enter", commit_once)
            # Escape used to be `lambda _: self.render()`. render() is async, so
            # the lambda built a coroutine and dropped it on the floor — nothing
            # was awaited and nothing happened. The box stayed open with no way
            # out but clicking back into it and pressing Enter, which committed
            # a change nobody asked for.
            box.on("keydown.escape", cancel)
            box.on("blur", blur)
            ui.timer(0.01, suggest, once=True)

    def _open_in_elements(self, name: str) -> None:
        """
        Hand the element itself over to the Elements screen.

        Deliberately a navigation rather than another dialog here: editing an
        element is not editing a step. On that screen the group and selector are
        mandatory, the name is normalised, duplicates are checked, and a rename
        shows every test and step group it will rewrite before it does it. None
        of that belongs in a popover over one step.
        """
        if not name:
            return
        ui.navigate.to(f"/platform/{self.platform}/elements?edit={name}")

    def _create_locator_from(self, current_name: str, seg: dict,
                             group: str, selector: str) -> None:
        """Fork a shared locator into a new one, then repoint just this step."""
        from ui.pages.platform.elements import open_create_element_dialog

        start, end = seg.get("start", 0), seg.get("end", 0)
        open_create_element_dialog(
            self.platform,
            group_hint=group,
            name_placeholder=f"{current_name}_variant",
            selector_hint=selector,
            title=f"Create a locator from '{current_name}'",
            intro="This opens the same create-element popup used on the Elements "
                  "screen. Save it here and only this step will point at the "
                  "new locator.",
            on_saved=lambda saved: self._apply(start, end, saved),
        )

    def _apply(self, start: int, end: int, value: str) -> None:
        """Splice the new value into the step and hand the whole line back."""
        self.step = self.step[:start] + value + self.step[end:]
        self.on_change(self.step)


#: How the author knows the element, and how that becomes a selector. Offering a
#: type is not decoration: pasting `submit-otp` into a field labelled "selector"
#: produces a locator that matches nothing, and the failure appears much later.
LOCATOR_TYPES = {
    "xpath":     ("XPath",            "//button[@id='submit-otp']",        "{v}"),
    "css":       ("CSS selector",     "form.login button.continue",        "{v}"),
    "id":        ("Element id",       "submit-otp",                        "#{v}"),
    "testid":    ("Test id",          "otp-submit",                        "[data-testid='{v}']"),
    "name":      ("name attribute",   "mobile_number",                     "[name='{v}']"),
    "text":      ("Visible text",     "Maybe Later",                       "//*[normalize-space(text())='{v}']"),
    "aria":      ("aria-label",       "Close dialog",                      "[aria-label='{v}']"),
}


#: How a selector written straight into a step announces what kind it is.
_SELECTOR_KIND = (
    ("xpath=", "xpath"), ("css=", "css"),
    ("//", "xpath"), ("(//", "xpath"), ("(", "xpath"),
)


def _split_selector(raw: str) -> tuple[str, str]:
    """(kind, value) for a selector typed into a step, or ("", "")."""
    v = (raw or "").strip()
    if not v:
        return "", ""
    for prefix, kind in _SELECTOR_KIND:
        if v.startswith(prefix):
            return kind, (v[len(prefix):] if prefix.endswith("=") else v)
    return "", ""


def _unknown_locator_dialog(name: str, platform: str, *,
                            on_saved: Callable[[str], None],
                            on_use_anyway: Callable[[], None],
                            step: str = "", group_hint: str = "",
                            selector_hint: str = "") -> None:
    """
    Record an element the database does not have, without leaving the step.

    The point is that "needs a locator" should not be a dead end. Everything the
    database requires is asked for here — how you know the element, the value
    itself, a group, and a name — and everything it can work out for itself is
    worked out: the selector is built from the type, the name is proposed from
    the selector, duplicates are checked as you type, and the editor snippets are
    refreshed on save so the new element turns up in suggestions immediately.

    Accepting an unknown name silently is the alternative, and it produces a flow
    that lints clean until the moment it runs.
    """
    state = {"suggested": [], "checked_ok": False}
    # A selector already written into the step is the answer to the first two
    # questions this form asks, so it answers them rather than asking again.
    hint_kind, hint_value = _split_selector(selector_hint)
    dialog = ui.dialog().props("persistent")
    with dialog, ui.card().style("width:40rem"):
        ui.label(f"'{name}' is not in your element list" if name
                 else "Add an element").style(
            f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
        ui.label("Save it here and the step works everywhere — no copying into a "
                 "file, no restart.").style(
            f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

        kind = ui.select({k: v[0] for k, v in LOCATOR_TYPES.items()},
                         value=hint_kind or "xpath",
                         label="How do you identify it?") \
            .props("outlined dense").classes("w-full")
        raw = ui.input("Paste it here", value=hint_value) \
            .props("outlined dense").classes("w-full") \
            .style(f"font-family:{TYPOGRAPHY['mono']}")
        if hint_value:
            ui.label("Taken from the step — it already had this selector in it.") \
                .style(f"font-size:{TYPOGRAPHY['size_xs']};"
                       f"color:{COLORS['primary']}")
        example = ui.label().style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
            f"font-family:{TYPOGRAPHY['mono']}")
        built = ui.label().style(
            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['primary']};"
            f"font-family:{TYPOGRAPHY['mono']}")

        group = ui.input("Group / page", value=group_hint,
                         placeholder="home_page").props("outlined dense").classes("w-full")
        el_name = ui.input("Element name", value=name).props("outlined dense").classes("w-full")
        suggest_row = ui.row().classes("w-full items-center gap-2 flex-wrap")
        verdict = ui.column().classes("w-full gap-0")

        def selector_now() -> str:
            v = (raw.value or "").strip()
            if not v:
                return ""
            return LOCATOR_TYPES[kind.value or "xpath"][2].format(v=v)

        def show_example() -> None:
            example.set_text("e.g. " + LOCATOR_TYPES[kind.value or "xpath"][1])
            built.set_text("")

        async def recheck() -> None:
            sel = selector_now()
            built.set_text(f"saved as: {sel}" if sel else "")
            verdict.clear()
            suggest_row.clear()
            state["checked_ok"] = False
            if not sel:
                return
            try:
                res = await api.check_locator_full(
                    (el_name.value or "").strip(), (group.value or "").strip(),
                    sel, platform, step)
            except api.ApiError:
                return

            state["suggested"] = res.get("suggested_names", [])
            with suggest_row:
                if state["suggested"]:
                    ui.label("Suggested:").style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}")
                    for sug in state["suggested"]:
                        ui.button(sug, on_click=lambda v=sug: (el_name.set_value(v),
                                                               recheck())) \
                            .props("flat dense").style(
                                f"font-family:{TYPOGRAPHY['mono']};"
                                f"font-size:{TYPOGRAPHY['size_xs']};"
                                f"color:{COLORS['primary']}")
                elif (raw.value or "").strip():
                    ui.label("Nothing in that selector describes the element, so "
                             "there is no name worth suggesting — name it after "
                             "what it does.").style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}")

            with verdict:
                if res.get("changed") and res.get("normalised"):
                    ui.label(f"Will be saved as “{res['normalised']}”").style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['primary']}")
                if res.get("reason"):
                    ui.label(res["reason"]).style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")
                for cf in res.get("conflicts", []):
                    colour = (COLORS["danger"] if cf["severity"] == "blocking"
                              else COLORS["warning"])
                    ui.label(cf["message"]).style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{colour}")
                    ui.label(cf["why"]).style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['text_muted']}")
                if res.get("ok") and not res.get("reason"):
                    ui.label("✓ name and selector are both free").style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['success']}")
                    state["checked_ok"] = True

        kind.on_value_change(lambda: (show_example(), recheck()))
        raw.on_value_change(recheck)
        el_name.on_value_change(recheck)
        group.on_value_change(recheck)
        show_example()
        # The duplicate and naming checks only ran on a keystroke. With the
        # selector filled in from the step there is no keystroke, so the form
        # would have opened showing no verdict at all.
        if hint_value:
            ui.timer(0.05, recheck, once=True)

        async def save(force: bool = False) -> None:
            sel = selector_now()
            if not sel:
                ui.notify("Paste the selector first", type="warning")
                return
            if not (group.value or "").strip():
                ui.notify("A group is required — it is how elements stay findable",
                          type="warning")
                return
            if not (el_name.value or "").strip():
                ui.notify("Give it a name, or take one of the suggestions",
                          type="warning")
                return
            try:
                res = await api.add_locator((group.value or "").strip(),
                                            (el_name.value or "").strip(),
                                            sel, force=force)
            except api.ApiError as e:
                detail = e.detail
                if e.status == 409 and isinstance(detail, dict):
                    with verdict:
                        ui.label(detail.get("message", "")).style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")
                        ui.button("Save anyway", on_click=lambda: save(True)) \
                            .props("flat dense color=negative")
                    return
                ui.notify(str(detail)[:160], type="negative")
                return

            saved = res.get("normalised", {}).get("name") or (el_name.value or "").strip()
            # Refresh the editor snippets so the new element appears in
            # suggestions straight away rather than after a restart.
            try:
                await api.resync_snippets()
            except api.ApiError:
                pass
            if res.get("warning"):
                ui.notify(res["warning"], type="warning", timeout=8000)
            else:
                ui.notify(f"Saved {saved} — now available in every test",
                          type="positive")
            dialog.close()
            on_saved(saved)

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            ui.button("Use without saving",
                      on_click=lambda: (dialog.close(), on_use_anyway())).props("flat") \
                .tooltip("The step will not run until this element exists")
            ui.button("Save element", on_click=lambda: save(False)).props("unelevated")
    dialog.open()
