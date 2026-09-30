"""
ui/pages/issues.py — review the failures of a run and raise them on Jira.

/issues?plan_run=PR_…     every failed test case of a plan run
/issues?run_id=…          one test case run (Run Center / test case page)

Top to bottom:
  1. where the issues go — story ticket (project + label + link come from it),
     site (live or not, which decides Bug vs Defect/Concern)
  2. your Jira connection — raise under your own name, once per person
  3. common inputs, asked once and remembered — owners (→ Assignee) and
     severity (→ Priority), each with "use for all"
  4. the issues — tick, type, summary, details, screenshots, possible duplicates
  5. Raise on Jira · Open in Jira (prefilled) · Download CSV
"""
from __future__ import annotations

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY

PRIORITIES = ["Highest", "High", "Medium", "Low", "Lowest"]
TYPE_TINT = {"Bug": "danger", "Defect": "warning", "Concern": "accent"}


def _type_rule(live, confirmed: bool) -> tuple[str, list[str]]:
    if live:
        return ("Bug", ["Bug"]) if confirmed else ("Concern", ["Bug", "Concern"])
    return ("Defect" if confirmed else "Concern"), ["Defect", "Concern"]


def _muted(text: str, size: str = "size_xs"):
    return ui.label(text).style(f"font-size:{TYPOGRAPHY[size]}; color:{COLORS['text_muted']}")


class IssuesPage:
    def __init__(self, run_id: str, plan_run: str) -> None:
        self.run_id, self.plan_run = run_id, plan_run
        self.d: dict = {}
        self.people: dict[str, str] = {}          # jira name -> "Display (email)"
        self.state: dict[str, dict] = {}          # issue id -> editable fields
        self.common = {"owner_Defect": "", "owner_Concern": "", "owner_Bug": "",
                       "priority": "Medium", "same_owner": True, "same_priority": True}

    async def load(self) -> None:
        self.d = await api.issue_draft(self.run_id, self.plan_run)
        defaults = (self.d.get("login") or {}).get("defaults") or {}
        self.common["owner_Defect"] = defaults.get("defect_owner", "")
        self.common["owner_Concern"] = defaults.get("concern_owner", "")
        self.common["owner_Bug"] = defaults.get("bug_owner", "")
        self.common["priority"] = defaults.get("priority") or "Medium"
        info = self.d.get("story_info") or {}
        if info.get("project"):
            try:
                for u in await api.jira_assignees(info["project"]["key"]):
                    self.people[u["name"]] = f"{u['display']}" + (f"  ({u['email']})" if u.get("email") else "")
            except api.ApiError:
                pass
        for i in self.d.get("issues", []):
            self.state[i["id"]] = {"pick": not i.get("raised"), "type": i["type"],
                                   "confirmed": i["confirmed"], "summary": i["summary"],
                                   "description": i["description"], "priority": "",
                                   "owner": ""}

    # ── layout ──────────────────────────────────────────────────────────────
    def render(self) -> None:
        ctx = self.d.get("context", {})
        with ui.column().classes("w-full gap-3 p-4").style("max-width:80rem"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.button(icon="arrow_back", on_click=lambda: ui.navigate.back()).props("flat dense")
                ui.label(f"Issues from {'plan run' if ctx.get('kind') == 'plan' else 'run'}: "
                         f"{ctx.get('name', '')}").style(
                    f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            self._where()
            self._login()
            self.common_box = ui.column().classes("w-full")
            self._common()
            self.list_box = ui.column().classes("w-full gap-2")
            self._issues()
            self._automation()
            self.actions = ui.row().classes("w-full items-center gap-2")
            self._actions()

    def _card(self, title: str):
        c = ui.column().classes("w-full gap-2").style(
            f"border:1px solid {COLORS['border']}; border-radius:8px; padding:12px 14px")
        with c:
            ui.label(title).style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
        return c

    def _where(self) -> None:
        live = self.d.get("live")
        with self._card("Where the tickets go"):
            with ui.row().classes("w-full items-center gap-4"):
                self.story_in = ui.input("Story ticket", value=self.d.get("story", ""),
                                         placeholder="GJDT-22417").props("outlined dense") \
                    .style("width:14rem")
                info = self.d.get("story_info") or {}
                if info:
                    _muted(f"{info.get('summary', '')[:90]} · project {info['project']['name']} "
                           f"({info['project']['key']}) · label {info['key']} · linked as 'Relates'",
                           "size_sm")
                elif self.d.get("jira_error"):
                    ui.label(self.d["jira_error"]).style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")
            site = ("live site — confirmed issues are Bugs; unconfirmed: Bug or Concern" if live
                    else "not live (development / staging / pre-prod) — Defect or Concern"
                    if live is False else "site unknown — treated as not live")
            _muted(f"Site: {site}", "size_sm")

    def _login(self) -> None:
        lg = self.d.get("login") or {}
        with self._card("Your Jira account"):
            if lg.get("connected"):
                with ui.row().classes("items-center gap-2"):
                    ui.icon("verified_user").style(f"color:{COLORS['success']}")
                    ui.label(f"Tickets will be raised as {lg.get('display') or lg.get('email')} "
                             f"({lg.get('email')})").style(f"font-size:{TYPOGRAPHY['size_sm']}")
                    ui.button("Change", on_click=self._login_dialog).props("flat dense no-caps")
            else:
                _muted("Connect once so tickets are raised under your own name, with the "
                       "screenshots attached. Without it you can still open each ticket in "
                       "Jira pre-filled, or download a CSV for bulk upload.", "size_sm")
                ui.button("Connect Jira", icon="link", on_click=self._login_dialog) \
                    .props("unelevated dense")

    def _login_dialog(self) -> None:
        base = "https://jdjira.justdial.com"
        dialog = ui.dialog()
        with dialog, ui.card().style("width:34rem"):
            ui.label("Connect your Jira account").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            _muted("1. Log in to Jira in your browser with your Justdial email.\n"
                   "2. Open Profile → Personal Access Tokens → Create token (any name, "
                   "no expiry or 1 year) and copy it.\n3. Paste it here. It is kept on "
                   "this server for your login only and never shown again.", "size_sm") \
                .style("white-space:pre-line")
            ui.link("Open Jira → Personal Access Tokens",
                    f"{base}/secure/ViewProfile.jspa?selectedTab=com.atlassian.pats.pats-plugin:"
                    f"jira-user-personal-access-tokens", new_tab=True)
            email = ui.input("Justdial email", placeholder="name@justdial.com") \
                .props("outlined dense").classes("w-full")
            token = ui.input("Personal Access Token", password=True, password_toggle_button=True) \
                .props("outlined dense").classes("w-full")
            note = ui.label("").style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['danger']}")

            async def save() -> None:
                try:
                    res = await api.save_jira_login(email.value or "", token.value or "")
                except api.ApiError as e:
                    note.set_text(str(e.detail))
                    return
                token.set_value("")
                dialog.close()
                ui.notify(f"Connected as {res.get('display') or res.get('email')}", type="positive")
                ui.navigate.reload()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Check & save", on_click=save).props("unelevated")
        dialog.open()

    # ── common inputs ───────────────────────────────────────────────────────
    def _types_present(self) -> list[str]:
        seen = []
        for i in self.d.get("issues", []):
            st = self.state[i["id"]]
            if st["pick"] and st["type"] not in seen:
                seen.append(st["type"])
        return [t for t in ("Bug", "Defect", "Concern") if t in seen] or ["Defect"]

    def _owner_select(self, label: str, value: str, on_change, width="18rem"):
        opts = {"": "— nobody —", **self.people}
        if value and value not in opts:
            opts[value] = value
        return ui.select(opts, value=value, label=label, with_input=True,
                         new_value_mode="add-unique", on_change=on_change) \
            .props("outlined dense").style(f"width:{width}")

    def _common(self) -> None:
        self.common_box.clear()
        with self.common_box, self._card("Common details (asked once, remembered for next time)"):
            with ui.row().classes("w-full items-end gap-4"):
                for t in self._types_present():
                    key = f"owner_{t}"
                    self._owner_select(f"{t} owner (Assignee)", self.common[key],
                                       lambda e, k=key: self._set_common(k, e.value))
                ui.select(PRIORITIES, value=self.common["priority"], label="Severity (Priority)",
                          on_change=lambda e: self._set_common("priority", e.value)) \
                    .props("outlined dense").style("width:12rem")
            with ui.row().classes("gap-4"):
                ui.checkbox("Same owner for all issues", value=self.common["same_owner"],
                            on_change=lambda e: self._set_common("same_owner", e.value, redraw=True))
                ui.checkbox("Same severity for all issues", value=self.common["same_priority"],
                            on_change=lambda e: self._set_common("same_priority", e.value, redraw=True))
            _muted("Project, label (the story key) and the 'Relates' link come from the story "
                   "ticket. Nobody is assigned unless you pick an owner.")

    def _set_common(self, key: str, value, redraw: bool = False) -> None:
        self.common[key] = value
        if redraw:
            self._issues()

    # ── issues ──────────────────────────────────────────────────────────────
    def _issues(self) -> None:
        self.list_box.clear()
        issues = self.d.get("issues", [])
        with self.list_box:
            ui.label(f"Issues found ({len(issues)})").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}; margin-top:6px")
            if not issues:
                _muted("No product failures in this run — nothing to raise.", "size_sm")
            for i in issues:
                self._issue(i)

    def _issue(self, i: dict) -> None:
        st = self.state[i["id"]]
        raised = i.get("raised")
        dups = (self.d.get("duplicates") or {}).get(i["id"], [])
        with ui.column().classes("w-full gap-1").style(
                f"border:1px solid {COLORS['border']}; border-radius:8px; padding:10px 12px;"
                f"background:{COLORS['success'] + '0D' if raised else 'transparent'}"):
            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                ui.checkbox(value=st["pick"] and not raised,
                            on_change=lambda e, s=st: (s.update(pick=e.value), self._common(),
                                                       self._actions())) \
                    .props("dense").set_enabled(not raised)
                tint = COLORS[TYPE_TINT.get(st["type"], "accent")]
                _, choices = _type_rule(i.get("live"), st["confirmed"])
                ui.select(choices, value=st["type"] if st["type"] in choices else choices[0],
                          on_change=lambda e, s=st: (s.update(type=e.value), self._common())) \
                    .props("dense borderless options-dense").style(
                        f"min-width:6.5rem; color:{tint}; font-weight:600")
                ui.switch("Confirmed", value=st["confirmed"],
                          on_change=lambda e, s=st, x=i: self._confirm(x, s, e.value)) \
                    .props("dense").tooltip("; ".join(i.get("reproduced") or [])
                                            or "Seen once — tick when you have confirmed it")
                ui.input(value=st["summary"], on_change=lambda e, s=st: s.update(summary=e.value)) \
                    .props("dense outlined").classes("flex-grow")
                if raised:
                    ui.link(f"{raised['key']} ↗", raised["url"], new_tab=True).style(
                        f"font-weight:600; color:{COLORS['success']}")
            with ui.row().classes("w-full items-center gap-2").style("padding-left:2rem"):
                for dev in i.get("devices", []):
                    ui.label(dev).style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; background:{COLORS['border']};"
                        "border-radius:10px; padding:1px 8px")
                _muted(f"{i['test_case']} · step: {i['step'][:80]}")
            if not self.common["same_owner"] or not self.common["same_priority"]:
                with ui.row().classes("items-end gap-3").style("padding-left:2rem"):
                    if not self.common["same_owner"]:
                        self._owner_select("Owner", st["owner"],
                                           lambda e, s=st: s.update(owner=e.value), "16rem")
                    if not self.common["same_priority"]:
                        ui.select(PRIORITIES, value=st["priority"] or self.common["priority"],
                                  label="Severity",
                                  on_change=lambda e, s=st: s.update(priority=e.value)) \
                            .props("outlined dense").style("width:10rem")
            for dup in dups:
                with ui.row().classes("items-center gap-1").style("padding-left:2rem"):
                    ui.icon("content_copy", size="16px").style(f"color:{COLORS['warning']}")
                    ui.label("Possibly already raised:").style(
                        f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['warning']}")
                    ui.link(f"{dup['key']} ({dup['type']}, {dup['status']}) — {dup['summary'][:70]}",
                            f"https://jdjira.justdial.com/browse/{dup['key']}", new_tab=True) \
                        .style(f"font-size:{TYPOGRAPHY['size_xs']}")
            with ui.expansion("Description & screenshots").classes("w-full") \
                    .style("padding-left:1.4rem"):
                ui.textarea(value=st["description"],
                            on_change=lambda e, s=st: s.update(description=e.value)) \
                    .props("outlined autogrow").classes("w-full") \
                    .style(f"font-family:{TYPOGRAPHY['mono']}; font-size:{TYPOGRAPHY['size_xs']}")
                with ui.row().classes("gap-2"):
                    for shot in i.get("screenshots", []):
                        ui.image(f"/screenshots/{shot}").style(
                            f"width:9rem; border:1px solid {COLORS['border']}; border-radius:4px")
                if i.get("video"):
                    _muted(f"Video attached too: {i['video']}")

    def _confirm(self, i: dict, st: dict, value: bool) -> None:
        st["confirmed"] = value
        default, choices = _type_rule(i.get("live"), value)
        if st["type"] not in choices:
            st["type"] = default
        elif i.get("live") and value:
            st["type"] = "Bug"
        self._issues()
        self._common()

    def _automation(self) -> None:
        items = self.d.get("automation") or []
        if not items:
            return
        with self._card(f"Fix the test, not the product ({len(items)}) — never raised"):
            for a in items:
                _muted(f"• {a['test_case']} [{a['device']}] — {a['kind']}: {a['reason']}", "size_sm")

    # ── actions ─────────────────────────────────────────────────────────────
    def _payload(self) -> list[dict]:
        out = []
        for i in self.d.get("issues", []):
            st = self.state[i["id"]]
            if not st["pick"] or i.get("raised"):
                continue
            owner = (self.common.get(f"owner_{st['type']}", "") if self.common["same_owner"]
                     else st["owner"])
            prio = self.common["priority"] if self.common["same_priority"] else (
                st["priority"] or self.common["priority"])
            out.append({**i, "type": st["type"], "summary": st["summary"],
                        "description": st["description"], "owner": owner or "",
                        "priority": prio, "confirmed": st["confirmed"]})
        return out

    def _actions(self) -> None:
        self.actions.clear()
        n = len(self._payload())
        lg = self.d.get("login") or {}
        with self.actions:
            ui.space()
            ui.button("Download CSV", icon="download", on_click=self._csv) \
                .props("flat").set_enabled(n > 0)
            ui.button("Open in Jira (pre-filled)", icon="open_in_new", on_click=self._prefill) \
                .props("flat").set_enabled(n > 0)
            b = ui.button(f"Raise {n} on Jira", icon="bug_report", on_click=self._raise) \
                .props("unelevated").style(f"background:{COLORS['danger']}")
            b.set_enabled(n > 0 and bool(lg.get("connected")))
            if not lg.get("connected"):
                b.tooltip("Connect your Jira account above first")

    async def _remember(self) -> None:
        try:
            await api.save_issue_defaults({
                "defect_owner": self.common["owner_Defect"] or "",
                "concern_owner": self.common["owner_Concern"] or "",
                "bug_owner": self.common["owner_Bug"] or "",
                "priority": self.common["priority"]})
        except api.ApiError:
            pass

    async def _raise(self) -> None:
        items = self._payload()
        story = (self.story_in.value or "").strip().upper()
        if not story:
            ui.notify("Enter the story ticket first", type="warning")
            return
        dialog = ui.dialog()
        with dialog, ui.card().style("width:34rem"):
            ui.label(f"Raise {len(items)} ticket(s) on Jira?").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            for it in items:
                _muted(f"• {it['type']} · {it['priority']} · owner {it['owner'] or 'nobody'} — "
                       f"{it['summary'][:80]}", "size_sm")
            _muted(f"Project and label from {story}, linked as 'Relates', screenshots attached.")

            async def go() -> None:
                dialog.close()
                await self._remember()
                n = ui.notification("Raising on Jira…", spinner=True, timeout=None)
                try:
                    res = await api.raise_issues(story, items)
                except api.ApiError as e:
                    n.dismiss()
                    ui.notify(str(e.detail), type="negative", timeout=10000)
                    return
                n.dismiss()
                ok = [r for r in res if r.get("ok")]
                bad = [r for r in res if not r.get("ok")]
                if ok:
                    ui.notify("Raised: " + ", ".join(r["key"] for r in ok), type="positive",
                              timeout=10000)
                for r in bad:
                    ui.notify(r.get("error", "failed"), type="negative", timeout=12000)
                for r in ok:
                    if r.get("warnings"):
                        ui.notify(f"{r['key']}: " + "; ".join(r["warnings"]), type="warning")
                await self.load()
                self._issues()
                self._actions()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Raise", icon="bug_report", on_click=go).props("unelevated") \
                    .style(f"background:{COLORS['danger']}")
        dialog.open()

    async def _prefill(self) -> None:
        story = (self.story_in.value or "").strip().upper()
        items = self._payload()
        try:
            links = await api.issue_prefill_links(story, items)
        except api.ApiError as e:
            ui.notify(str(e.detail), type="negative")
            return
        await self._remember()
        dialog = ui.dialog()
        with dialog, ui.card().style("width:40rem"):
            ui.label("Open each ticket in Jira").style(f"font-weight:{TYPOGRAPHY['weight_bold']}")
            _muted("Jira opens its own Create form, filled in. Attach the screenshots from the "
                   "report there (a link cannot carry files), then press Create.", "size_sm")
            for it in items:
                ui.link(f"{it['type']} — {it['summary'][:90]}", links.get(it["id"], "#"),
                        new_tab=True).style(f"font-size:{TYPOGRAPHY['size_sm']}")
            ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    async def _csv(self) -> None:
        story = (self.story_in.value or "").strip().upper()
        try:
            data = await api.issues_csv(story, self._payload())
        except api.ApiError as e:
            ui.notify(str(e.detail), type="negative")
            return
        await self._remember()
        ui.download(data, f"issues_{story or 'run'}.csv")


async def render(run_id: str = "", plan_run: str = "") -> None:
    try:
        platforms = await api.platforms()
    except api.ApiError:
        platforms = []
    sidebar(active="/history", platforms=platforms)
    topbar(["Execute", "Issues"], platforms=platforms)
    page = IssuesPage(run_id, plan_run)
    try:
        await page.load()
    except api.ApiError as e:
        ui.label(str(e.detail)).style(f"color:{COLORS['danger']}; padding:16px")
        return
    page.render()
