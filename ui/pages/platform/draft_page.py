"""
ui/pages/platform/draft_page.py — From prompt, as a page  (route: /platform/{platform}/draft)

Drafting test cases from a Jira ticket is a reading job: the ticket summary,
the model's questions, its assumptions, what it left unclear, and twenty
cases with their steps. That did not fit a dialog — the Generate button sat
below the fold with no scroll, and everything was the same grey.

This page lays the same work out top to bottom, with a colour per kind of
information so the eye lands on what needs a decision:

    green   what was READ from Jira (a checkable claim, not a hope)
    blue    QUESTIONS for the tester — answer here, re-draft
    amber   ASSUMPTIONS the model made (worth a glance, not a stop)
    red     UNCLEAR — deliberately not turned into steps
    P / N   each case's classification; `covers` chips trace it to the ticket

The dialog's "From prompt" tab now just opens this page.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY

#: The colour language of this page. Kept in one place so a legend and the
#: panels cannot drift apart.
TONE = {
    "read":       {"bg": f"{COLORS['success']}14", "line": COLORS["success"], "icon": "task_alt"},
    "question":   {"bg": f"{COLORS['primary']}12", "line": COLORS["primary"], "icon": "contact_support"},
    "assumption": {"bg": f"{COLORS['warning']}14", "line": COLORS["warning"], "icon": "lightbulb"},
    "unclear":    {"bg": f"{COLORS['danger']}12",  "line": COLORS["danger"],  "icon": "help_outline"},
}


class DraftPage:
    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.platforms: list = []
        self.res: dict = {}            # last draft response
        self.prompt_text = ""
        self.boxes: dict[str, ui.checkbox] = {}
        #: Spreadsheets the tester attached, read into text here in the UI.
        self.attachments: list[dict] = []
        #: Saved test case the drafted steps get appended to (optional).
        self.extend_flow = ""
        self.flow_names: list[str] = []
        self.busy = False              # a model call is in flight

    async def load(self, extend: str = "") -> None:
        try:
            self.platforms = await api.platforms()
        except api.ApiError:
            self.platforms = []
        try:
            projects = await api.list_projects(self.platform)
            self.flow_names = sorted(
                p.get("name", p) if isinstance(p, dict) else str(p) for p in projects)
        except api.ApiError:
            self.flow_names = []
        self.extend_flow = extend if extend in self.flow_names else ""

    # ── layout ──────────────────────────────────────────────────────────────
    def render(self) -> None:
        sidebar(active=f"/platform/{self.platform}", platforms=self.platforms)
        topbar(["Author", "Test Cases", "From prompt"], platforms=self.platforms)
        with ui.column().classes("w-full gap-4 p-4").style("max-width:76rem"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Draft test cases from a ticket or a description").style(
                    f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
                ui.space()
                ui.button("Back to test cases", icon="arrow_back",
                          on_click=lambda: ui.navigate.to(f"/platform/{self.platform}")) \
                    .props("flat dense")
            ui.label("Paste a Jira link and, under it, anything the ticket does not say "
                     "(environment, test mobile, where an OTP or a lead can be read from). "
                     "The ticket is read in full — story, sub-tasks, linked defects, comments. "
                     "Values you leave out are asked for at the end, never invented.").style(
                f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

            self.prompt = ui.textarea(
                placeholder="https://jdjira.justdial.com/browse/GJDT-12345\n"
                            "Answers from the tester:\n- Q: Environment? A: devx for …") \
                .props("outlined autogrow input-style=\"min-height:9rem\"").classes("w-full") \
                .style(f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_sm']}")
            # Manual cases as a sheet: attach an .xlsx/.csv here, or paste a
            # Google Sheets link in the prompt (shared "anyone with the link").
            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                ui.upload(label="Attach manual test cases (.xlsx / .csv)",
                          on_upload=self._on_upload, auto_upload=True, multiple=True) \
                    .props('accept=".xlsx,.xlsm,.csv" flat dense').style("max-width:26rem")
                self.att_list = ui.row().classes("items-center gap-2")
                ui.label("…or paste a Google Sheets link in the prompt (shared as "
                         "'Anyone with the link — Viewer').").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            # Add to a case that already exists (e.g. a lead check added later):
            # the model sees the saved steps and drafts only the addition, which
            # is appended to that case on Generate instead of creating new files.
            with ui.row().classes("w-full items-center gap-3 no-wrap"):
                self.extend_pick = ui.select(
                    {"": "— create new test case(s) —", **{n: n for n in self.flow_names}},
                    value=self.extend_flow, label="Add the drafted steps to an existing test case",
                    with_input=True, on_change=lambda e: setattr(self, "extend_flow", e.value or "")) \
                    .props("outlined dense options-dense").style("min-width:28rem")
                ui.label("Pick a case to extend it: only the new steps are drafted and they are "
                         "appended after its last step.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            with ui.row().classes("items-center gap-3"):
                self.draft_btn = ui.button("Draft testcases", icon="auto_awesome",
                                           on_click=self.draft) \
                    .props("unelevated").style(f"background:{COLORS['accent']}")
                self.spinner = ui.spinner(size="sm")
                self.spinner.set_visibility(False)
                self.status = ui.label("").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                ui.space()
                self._legend()

            self.result = ui.column().classes("w-full gap-3")

    def _legend(self) -> None:
        with ui.row().classes("items-center gap-3"):
            for label, tone in (("read from Jira", "read"), ("question for you", "question"),
                                ("assumption", "assumption"), ("unclear", "unclear")):
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.element("div").style(
                        f"width:10px; height:10px; border-radius:2px;"
                        f"background:{TONE[tone]['line']}")
                    ui.label(label).style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

    async def _on_upload(self, e) -> None:
        from ai_flow_builder.sheet_source import SheetError, read_sheet_bytes
        # NiceGUI 3.x: the upload event carries `e.file` (async read); the old
        # `e.name` / `e.content` no longer exist, so every upload failed.
        name = getattr(e.file, "name", "upload")
        try:
            text = read_sheet_bytes(name, await e.file.read())
        except SheetError as err:
            ui.notify(str(err), type="negative")
            return
        except Exception as err:  # noqa: BLE001
            ui.notify(f"Could not read {name}: {err}", type="negative")
            return
        self.attachments = [a for a in self.attachments if a["name"] != name]
        self.attachments.append({"name": name, "text": text})
        rows = text.count("\n")
        with self.att_list:
            with ui.row().classes("items-center gap-1 no-wrap").style(
                    f"background:{TONE['read']['bg']}; border-radius:12px; padding:2px 8px"):
                ui.icon("table_view").style(f"color:{TONE['read']['line']}; font-size:1rem")
                ui.label(f"{name} · {rows} rows").style(f"font-size:{TYPOGRAPHY['size_xs']}")
        ui.notify(f"Read {name} ({rows} rows) — it will be used as the baseline", type="positive")

    # ── drafting ────────────────────────────────────────────────────────────
    async def draft(self, text: str | None = None, redraft: bool = False) -> None:
        text = (text if text is not None else (self.prompt.value or "")).strip()
        if len(text) < 15:
            ui.notify("Paste a Jira link or describe what to test", type="warning")
            return
        if self.busy:
            ui.notify("Still drafting — one request at a time", type="warning")
            return
        self.busy = True
        self.draft_btn.set_enabled(False)
        self.spinner.set_visibility(True)
        self.status.set_text("Reading the ticket, then drafting — 2 to 6 minutes for a full story…")
        ui.notify("Re-drafting with your answers — 2 to 6 minutes; the page refreshes when done"
                  if redraft else "Drafting — 2 to 6 minutes for a full story",
                  type="info", timeout=12000)
        try:
            res = await api.draft_from_prompt(text, self.platform, redraft=redraft,
                                              attachments=self.attachments,
                                              extend_flow=self.extend_flow)
        except api.ApiError as err:
            ui.notify(err.detail, type="negative", timeout=15000)
            self.status.set_text(str(err.detail)[:200])
            return
        finally:
            self.busy = False
            self.spinner.set_visibility(False)
            self.draft_btn.set_enabled(True)
        self.res = res
        self.prompt_text = text
        cases = res.get("draft_testcases") or []
        kept = res.get("kept_ids") or []
        self.status.set_text(
            f"{len(cases)} addition(s) drafted for {self.extend_flow}" if self.extend_flow else
            f"{len(cases)} testcases — {len(kept)} kept from the earlier draft, "
            f"{len(cases) - len(kept)} new or changed" if kept else
            f"{len(cases)} testcases drafted by {(res.get('extra') or {}).get('model', 'the model')}"
            + ("  (from cache — same prompt as before)" if res.get("cached") else ""))
        self._render_result()

    async def redraft_with_answers(self, answers: dict[str, str]) -> None:
        lines = [f"- Q: {q}\n  A: {a.strip()}" for q, a in answers.items() if a.strip()]
        if not lines:
            ui.notify("Type at least one answer", type="warning")
            return
        text = self.prompt_text + "\n\nAnswers from the tester:\n" + "\n".join(lines)
        self.prompt.set_value(text)
        await self.draft(text, redraft=True)

    # ── result ──────────────────────────────────────────────────────────────
    def _panel(self, tone: str, title: str, *, open_: bool = False):
        t = TONE[tone]
        exp = ui.expansion(title, icon=t["icon"], value=open_).classes("w-full").style(
            f"background:{t['bg']}; border-left:4px solid {t['line']}; border-radius:6px")
        return exp

    def _render_result(self) -> None:
        res = self.res
        self.result.clear()
        self.boxes = {}
        with self.result:
            # what was read
            for j in res.get("jira") or []:
                with ui.row().classes("items-center gap-2 w-full no-wrap").style(
                        f"background:{TONE['read']['bg']}; border-left:4px solid "
                        f"{TONE['read']['line']}; border-radius:6px; padding:8px 12px"):
                    ui.icon(TONE["read"]["icon"]).style(f"color:{TONE['read']['line']}")
                    ui.label(f"Read {j.get('key')} ({j.get('type')}, {j.get('status')}): "
                             f"{j.get('summary', '')} — {len(j.get('subtasks', []))} sub-task(s), "
                             f"{len(j.get('linked', []))} linked defect(s)/concern(s), "
                             f"{j.get('comments', 0)} comment(s)").style(
                        f"font-size:{TYPOGRAPHY['size_sm']}")

            for heading in res.get("sheets") or []:
                with ui.row().classes("items-center gap-2 w-full no-wrap").style(
                        f"background:{TONE['read']['bg']}; border-left:4px solid "
                        f"{TONE['read']['line']}; border-radius:6px; padding:8px 12px"):
                    ui.icon("table_view").style(f"color:{TONE['read']['line']}")
                    ui.label(heading.replace("=", "").strip()).style(f"font-size:{TYPOGRAPHY['size_sm']}")

            # questions
            qs = res.get("questions") or []
            if qs:
                with self._panel("question", f"{len(qs)} question(s) for you — answer and re-draft",
                                 open_=True):
                    answer_boxes: dict[str, ui.input] = {}
                    with ui.column().classes("w-full gap-2").style("padding:4px 8px 10px"):
                        for q in qs:
                            qt = q.get("question", "")
                            ui.label(qt).style(
                                f"font-size:{TYPOGRAPHY['size_sm']};"
                                f"font-weight:{TYPOGRAPHY['weight_medium']}")
                            if q.get("why"):
                                ui.label(q["why"]).style(
                                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                            answer_boxes[qt] = ui.input(placeholder=q.get("example") or "your answer") \
                                .props("outlined dense").classes("w-full")
                        with ui.row().classes("items-center gap-3"):
                            ans_btn = ui.button("Answer & re-draft", icon="autorenew") \
                                .props("unelevated dense").style(f"background:{COLORS['primary']}")
                            ans_spin = ui.spinner(size="sm")
                            ans_spin.set_visibility(False)
                            ans_msg = ui.label("").style(
                                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

                        async def _answer(ans_btn=ans_btn, ans_spin=ans_spin, ans_msg=ans_msg,
                                          answer_boxes=answer_boxes) -> None:
                            answers = {q: (b.value or "") for q, b in answer_boxes.items()}
                            if not any(a.strip() for a in answers.values()):
                                ui.notify("Type at least one answer", type="warning")
                                return
                            ans_btn.set_enabled(False)
                            ans_spin.set_visibility(True)
                            ans_msg.set_text("Re-drafting with your answers — 2 to 6 minutes. "
                                             "Do not click again; the list below refreshes when done.")
                            try:
                                await self.redraft_with_answers(answers)
                            finally:
                                # The panel is re-rendered on success; on failure it stays
                                # and the button must come back.
                                try:
                                    ans_btn.set_enabled(True)
                                    ans_spin.set_visibility(False)
                                    ans_msg.set_text("")
                                except Exception:  # noqa: BLE001 — already re-rendered
                                    pass
                        ans_btn.on_click(_answer)

            # assumptions / unclear
            for key, tone, title in (("assumptions", "assumption", "assumption(s) made"),
                                     ("unclear", "unclear", "thing(s) left unclear — not turned into steps")):
                items = res.get(key) or []
                if items:
                    with self._panel(tone, f"{len(items)} {title}"):
                        with ui.column().classes("gap-1").style("padding:4px 8px 8px"):
                            for a in items:
                                ui.label(f"• {a}").style(f"font-size:{TYPOGRAPHY['size_xs']}")

            # the cases
            cases = res.get("draft_testcases") or []
            if not cases:
                ui.label("No testcases came back.").style(f"color:{COLORS['text_muted']}")
                return
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("Testcases — open one to read its steps; untick any you do not want").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; font-weight:{TYPOGRAPHY['weight_medium']}")
                ui.space()
                pos = sum(1 for c in cases if (c.get("classification") or "").lower().startswith("p"))
                ui.label(f"{pos} positive · {len(cases) - pos} negative").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                ui.button("All", on_click=lambda: [b.set_value(True) for b in self.boxes.values()]) \
                    .props("flat dense size=sm")
                ui.button("None", on_click=lambda: [b.set_value(False) for b in self.boxes.values()]) \
                    .props("flat dense size=sm")
            kept = set(res.get("kept_ids") or [])
            with ui.column().classes("w-full gap-0").style(
                    f"border:1px solid {COLORS['border']}; border-radius:6px"):
                for tc in cases:
                    self._case_row(tc, kept)

            # generate
            with ui.row().classes("w-full items-center gap-3").style("margin-top:8px"):
                self.one_each = ui.switch("One test case per drafted case (recommended)", value=True)
                self.one_each.set_visibility(not self.extend_flow)
                self.flow_name = ui.input("Save as (only when merging into one)",
                                          placeholder="ask_more_photos") \
                    .props("outlined dense").style("width:22rem")
                self.flow_name.bind_visibility_from(self.one_each, "value", backward=lambda v: not v)
                ui.space()
                self.gen_btn = ui.button("Generate & save test cases", icon="bolt",
                                         on_click=self.generate) \
                    .props("unelevated").style(f"background:{COLORS['primary']}")

    def _case_row(self, tc: dict, kept: set) -> None:
        kind = (tc.get("classification") or "")[:1].upper()
        colour = COLORS["success"] if kind == "P" else COLORS["warning"]
        is_new = bool(kept) and tc["id"] not in kept
        with ui.row().classes("w-full items-start no-wrap gap-2").style(
                f"padding:6px 10px; border-bottom:1px solid {COLORS['border']};"
                + (f"background:{COLORS['primary']}08;" if is_new else "")):
            self.boxes[tc["id"]] = ui.checkbox(value=True).props("dense")
            ui.label(kind or "·").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; font-weight:700; color:#fff;"
                f"background:{colour}; border-radius:3px; width:1.2rem; text-align:center;"
                f"flex:none; margin-top:3px").tooltip(
                "Positive" if kind == "P" else "Negative" if kind == "N" else "")
            with ui.column().classes("gap-0").style("min-width:0; flex:1 1 auto"):
                with ui.row().classes("items-center gap-2 no-wrap"):
                    ui.label(tc["id"]).style(
                        f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                        f"font-weight:600")
                    if is_new:
                        ui.label("new / changed").style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['primary']};"
                            f"border:1px solid {COLORS['primary']}66; border-radius:3px; padding:0 4px")
                    for c in tc.get("covers") or []:
                        ui.label(str(c)).style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']};"
                            f"background:{COLORS['border']}; border-radius:3px; padding:0 4px")
                ui.label(tc.get("title", "")).style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}; white-space:normal")
                steps = tc.get("steps") or []
                with ui.expansion(f"{len(steps)} steps", icon="list").props("dense").classes("w-full") \
                        .style(f"font-size:{TYPOGRAPHY['size_xs']}"):
                    with ui.column().classes("gap-0").style("padding:0 0 6px 4px"):
                        for i, st in enumerate(steps, 1):
                            is_comment = st.strip().startswith("#")
                            ui.label(f"{'' if is_comment else str(i) + '  '}{st}").style(
                                f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']};"
                                f"color:{COLORS['text_muted'] if is_comment else COLORS['text']};"
                                f"white-space:pre-wrap; word-break:break-all")
                        if tc.get("expected"):
                            ui.label(f"Expected: {tc['expected']}").style(
                                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['success']};"
                                f"margin-top:4px")

    # ── generation ──────────────────────────────────────────────────────────
    @staticmethod
    def _to_steps(g: dict) -> tuple[list[str], dict]:
        steps, meta, n = [], {}, 0
        for s in g["steps"]:
            if not s.get("emits"):
                continue
            n += 1
            steps.append(s["statement"])
            meta[n] = {"action": s.get("action", ""), "locator": s.get("locator", ""),
                       "status": s.get("status", ""), "note": s.get("note", "")}
        return steps, meta

    async def generate(self) -> None:
        chosen = [k for k, b in self.boxes.items() if b.value]
        if not chosen:
            ui.notify("Tick at least one testcase", type="warning")
            return
        source_id = self.res.get("source_id")
        self.gen_btn.set_enabled(False)
        try:
            pending: list[tuple[str, list[str]]] = []   # (flow name, steps) – not saved yet
            if self.extend_flow:
                # Append every ticked case to the saved test case, each under a
                # comment header so the addition is visible in the editor.
                existing = (await api.get_project(self.extend_flow)).get("steps", [])
                added: list[str] = []
                for tc_id in chosen:
                    try:
                        g = await api.generate(source_id, [tc_id], self.platform,
                                               flow_name=self.extend_flow, persist=False)
                        steps, _meta = self._to_steps(g)
                        if steps:
                            added += [f"# ── added by AI: {tc_id}"] + steps
                    except api.ApiError as err:
                        ui.notify(f"{tc_id}: {err.detail}", type="warning")
                if added:
                    pending.append((self.extend_flow, list(existing) + added))
            elif self.one_each.value:
                for tc_id in chosen:
                    name = tc_id.lower()
                    try:
                        g = await api.generate(source_id, [tc_id], self.platform,
                                               flow_name=name, persist=False)
                        steps, _meta = self._to_steps(g)
                        if steps:
                            pending.append((name, steps))
                    except api.ApiError as err:
                        ui.notify(f"{tc_id}: {err.detail}", type="warning")
            else:
                name = (self.flow_name.value or "").strip() or chosen[0].lower()
                g = await api.generate(source_id, chosen, self.platform,
                                       flow_name=name, persist=False)
                steps, _meta = self._to_steps(g)
                if steps:
                    pending.append((name, steps))
            if not pending:
                ui.notify("Nothing could be generated", type="negative")
                return
            # Ask for values FIRST – the tester can point an invented name at an
            # existing Test Data entry, and the steps are rewritten before saving.
            # Which drafted testcase uses which variable – so a URL shared for one
            # purpose is not silently filled into a name that other cases use too.
            usage: dict[str, list[str]] = {}
            for tc in self.res.get("draft_testcases") or []:
                tid = tc.get("testcase_id", "")
                if tid not in chosen:
                    continue
                for var in set(re.findall(r"\$\{([A-Za-z0-9_]+)\}", "\n".join(tc.get("steps") or []))):
                    usage.setdefault(var, []).append(tid)
            renames = await self._collect_inputs([st for _n, ss in pending for st in ss], usage)
            saved = []
            for name, steps in pending:
                if renames:
                    steps = [_apply_renames(st, renames) for st in steps]
                await api.save_project(name, steps, self.platform)
                saved.append(name)
            ui.notify(f"Saved {len(saved)} test case(s)", type="positive", timeout=8000)
            ui.navigate.to(f"/platform/{self.platform}?flow={quote(saved[0], safe='')}")
        except api.ApiError as err:
            ui.notify(f"Generation failed: {err.detail}", type="negative")
        finally:
            self.gen_btn.set_enabled(True)

    async def _collect_inputs(self, steps: list[str],
                              usage: dict[str, list[str]] | None = None) -> dict[str, str]:
        """Every value the generated steps need, asked once, pre-filled.

        Returns {invented_name: existing_test_data_name} for the variables the
        tester chose to map onto something already stored.
        """
        try:
            from ai_flow_builder.emitter import run_parameters
            needed = run_parameters([st for st in steps if not st.startswith("#")])
        except Exception:  # noqa: BLE001
            needed = []
        if not needed:
            return {}
        try:
            have = (await api.testdata("")).get("values", {}) or {}
        except api.ApiError:
            have = {}
        found = self.res.get("values_found") or {}
        cands = [u for u in (self.res.get("candidate_urls") or []) if isinstance(u, str)]
        why = {i.get("name"): i.get("why", "") for i in (self.res.get("inputs_needed") or [])
               if isinstance(i, dict)}
        NEW = "— enter a new value —"
        done = asyncio.Event()
        renames: dict[str, str] = {}
        with ui.dialog().props("persistent") as ask, ui.card().style(
                "width:56rem; max-width:95vw; max-height:90vh; overflow-y:auto"):
            ui.label("Values these test cases need").style(
                f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            ui.label("The drafter invented these names. For each one either pick an existing "
                     "Test Data entry (the steps are rewritten to use it – no duplicate URLs) "
                     "or give a value, which is saved under Test Data. Leave blank to be asked "
                     "at run time.") \
                .style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            fields: dict[str, ui.input] = {}
            picks: dict[str, ui.select] = {}
            names: dict[str, ui.input] = {}
            for n in needed:
                existing = have.get(n, {})
                if existing.get("defined"):
                    continue  # already stored under this exact name – nothing to ask
                prefill = found.get(n, "") or _guess_url(n, cands)
                matches = _similar_names(n, have)
                options = {NEW: NEW}
                for m in matches:
                    val = have[m].get("value", "")
                    if have[m].get("is_secret"):
                        val = "••••"
                    options[m] = f"${{{m}}}  =  {str(val)[:70]}"
                with ui.column().classes("w-full gap-0").style(
                        f"border:1px solid {COLORS['border']}; border-radius:8px; padding:6px 10px"):
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        names[n] = ui.input("name (rename to what it really holds)", value=n) \
                            .props("outlined dense prefix=\"${\" suffix=\"}\"").classes("w-full") \
                            .style(f"font-family:{TYPOGRAPHY['mono']}; font-weight:{TYPOGRAPHY['weight_bold']}")
                    used_in = (usage or {}).get(n) or []
                    if used_in:
                        ui.label("used by: " + ", ".join(used_in)).style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                    if why.get(n):
                        ui.label(why[n]).style(
                            f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
                    picks[n] = ui.select(options, value=NEW, label="Use existing Test Data")\
                        .props("outlined dense options-dense").classes("w-full")
                    fields[n] = ui.input("value", value=prefill,
                                         placeholder="new value (URL / text)") \
                        .props("outlined dense").classes("w-full") \
                        .style(f"font-family:{TYPOGRAPHY['mono']}")
                    if matches and not prefill:
                        picks[n].set_value(matches[0])

                    def _toggle(e, n=n) -> None:
                        fields[n].set_visibility(e.value == NEW)
                    picks[n].on_value_change(_toggle)
                    fields[n].set_visibility(picks[n].value == NEW)
            if not fields:
                ask.close(); done.set()

            async def save_all() -> None:
                saved = 0
                for n, box in fields.items():
                    pick = picks[n].value
                    if pick and pick != NEW:
                        renames[n] = pick
                        continue
                    new_name = re.sub(r"[^A-Za-z0-9_]", "_", (names[n].value or "").strip()) or n
                    v = (box.value or "").strip()
                    if new_name != n:
                        renames[n] = new_name
                    if not v:
                        continue
                    dup = next((k for k, d in have.items()
                                if d.get("defined") and str(d.get("value", "")) == v), None)
                    if dup:
                        renames[n] = dup   # same value already stored – reuse, don't duplicate
                        continue
                    try:
                        await api.set_testdata(new_name, v, updating=new_name in have, force=True)
                        saved += 1
                    except api.ApiError as e:
                        ui.notify(f"{n}: {e.detail}", type="warning")
                msg = []
                if saved:
                    msg.append(f"saved {saved} value(s) under Test Data")
                if renames:
                    msg.append(f"reused {len(renames)} existing name(s)")
                if msg:
                    ui.notify(", ".join(msg), type="positive")
                ask.close(); done.set()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Skip for now", on_click=lambda: (ask.close(), done.set())).props("flat")
                ui.button("Save & continue", icon="check", on_click=save_all).props("unelevated")
        if fields:
            ask.open()
        await done.wait()
        return renames


_STOP = {"url", "link", "page", "the", "and", "for", "of", "live", "devx", "test", "data",
         "value", "input", "var", "variable"}


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", name.lower()) if t and t not in _STOP}


def _similar_names(name: str, have: dict) -> list[str]:
    """Existing Test Data names sharing words with an invented one, best first."""
    mine = _tokens(name)
    if not mine:
        return []
    scored = []
    for k, d in have.items():
        if not d.get("defined"):
            continue
        theirs = _tokens(k)
        common = len(mine & theirs)
        if common:
            scored.append((common / len(mine | theirs), common, k))
    scored.sort(reverse=True)
    return [k for _s, _c, k in scored[:6]]


_GENERIC = {"wholesalers", "dealers", "manufacturers", "suppliers", "services", "service",
            "product", "products", "category", "mumbai", "chennai", "delhi", "bangalore",
            "indore", "pune", "hyderabad", "kolkata", "www", "nct", "results", "result"}


def _guess_url(name: str, cands: list[str]) -> str:
    """The candidate URL whose path contains EVERY distinctive word of the name.

    A loose "most words in common" match picked Brick-Wholesalers for
    bulk_sms_link_road_url because both mention Mumbai/wholesalers; a wrong
    prefill is worse than an empty box, so this is strict and otherwise blank.
    """
    mine = _tokens(name) - _GENERIC
    if not mine or not cands:
        return ""
    for u in cands:
        path = _tokens(re.sub(r"^https?://[^/]+", "", u))
        if mine <= path:
            return u
    return ""


def _apply_renames(step: str, renames: dict[str, str]) -> str:
    for old, new in renames.items():
        step = re.sub(r"\$\{" + re.escape(old) + r"\}", "${" + new + "}", step)
    return step


async def render(platform: str, extend: str = "") -> None:
    page = DraftPage(platform)
    await page.load(extend)
    page.render()
