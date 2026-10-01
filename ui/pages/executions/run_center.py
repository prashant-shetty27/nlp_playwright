"""
ui/pages/executions/run_center.py — Run Center  (route: /run)

Launch panel for starting a test run. Accepts pre-filled context via query
params (?flow=…, ?platform=…).

Layout (as specified):
    Left  — What to run: platform selector, flow picker
    Right — How to run it: device, browser, headless
    Footer— [Run Now] → /run/live

Two deliberate divergences from the spec:

  NO SUBPROCESS. The spec shelled out to runner.py. Execution now goes through
  POST /tests/run, which validates the platform and browser engine, masks secret
  parameter values in logs, and records the run in a bounded store. Shelling out
  would bypass all four.

  IT COLLECTS INPUTS. The spec had no notion of them. A generated flow references
  ${variables} and cannot run until they have values, so the flow is scanned and
  every referenced variable is asked for here. Anything whose name looks like a
  secret is entered masked and sent in secret_parameters, so the value never
  reaches a log or the launch response.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from nicegui import ui

from ui import api_client as api
from ui.layout.sidebar import sidebar
from ui.layout.topbar import topbar
from ui.theme import COLORS, TYPOGRAPHY
#: The one definition, in nlp/variables. A private copy here agreed with
#: it today and had nothing keeping it in step tomorrow.
from nlp.variables import REFERENCE_RE as _VAR  # noqa: E402
#: Mirrors config.settings.is_secret_name. Duplicated deliberately: the browser
#: must decide whether to mask a FIELD before any request is made, and asking the
#: server what to hide would mean sending the value first.
_SECRET_HINT = ("otp", "password", "passwd", "pwd", "token", "secret", "apikey",
                "api_key", "auth", "mobile", "phone", "pin", "credential")


def _is_secret(name: str) -> bool:
    return any(h in name.lower() for h in _SECRET_HINT)



def _back_to_test_case(flow: str, platform: str) -> None:
    """
    A link back to the test case being run.

    Running a flow left you on the run screen with no route back to the thing
    you had just been editing — the browser Back button was the only way, and it
    does not survive a redirect to the live view.

    The flow name travels in the query string. Without it the link landed on the
    Test Cases screen with nothing open — a link that says "Back to X" and then
    does not show X reads as a failure, not as navigation.
    """
    if not flow:
        return
    href = f"/platform/{platform}?flow={quote(flow, safe='')}"
    with ui.row().classes("items-center gap-1 cursor-pointer") \
            .on("click", lambda: ui.navigate.to(href)):
        ui.icon("arrow_back").style(f"font-size:1rem; color:{COLORS['primary']}")
        ui.label(f"Back to {flow}").style(
            f"color:{COLORS['primary']}; font-size:{TYPOGRAPHY['size_sm']};"
            f"text-decoration:underline; text-underline-offset:3px")


class RunCenter:
    def __init__(self, flow: str = "", platform: str = "website") -> None:
        self.platform = platform or "website"
        self.flow = flow
        self.platforms: list[dict] = []
        self.devices: list[dict] = []
        self.projects: list[str] = []
        self.fields: dict[str, ui.input] = {}
        #: Names Test Data already supplies. Asked for as an OVERRIDE rather
        #: than as a requirement — a value you saved once should not have to be
        #: retyped every run, which is the entire point of saving it.
        self.provided: dict[str, dict] = {}

    async def load(self) -> None:
        try:
            self.platforms = await api.platforms()
            self.devices = await api.devices()
            raw = await api.list_projects()
            self.projects = sorted(p if isinstance(p, str) else p.get("name", "")
                                   for p in raw)
        except api.ApiError as e:
            ui.notify(f"Could not load: {e.detail}", type="negative")

    def _enabled(self) -> list[dict]:
        return [p for p in self.platforms if p.get("enabled", True)]

    def render(self) -> None:
        sidebar(active="/run", platforms=self.platforms)
        # Run Center IS the run form, so the top-bar button would only ever
        # repeat some other flow from history; hide it here by naming the open
        # flow (the form's RUN NOW is the real control).
        topbar(["Execute", "Run Center"], platforms=self.platforms,
               platform=self.platform,
               on_platform_change=self._set_platform,
               current_flow=self.flow or "")
        with ui.column().classes("w-full gap-3").style("padding:8px 16px 16px; max-width:90rem"):
            _back_to_test_case(self.flow, self.platform)
            # Top: what, where, on which browsers — and Run. Below: how (options)
            # beside the values the test needs. Rarely-touched settings fold away.
            self.top = ui.card().classes("w-full").style("padding:14px 16px")
            with ui.row().classes("w-full gap-4 items-start").style("flex-wrap:wrap"):
                self.left = ui.card().classes("gap-2").style(
                    "flex:1 1 26rem; min-width:22rem; padding:14px 16px")
                self.right = ui.card().classes("gap-3").style(
                    "flex:1 1 26rem; min-width:22rem; padding:14px 16px")
        self._render_left()
        self._render_right()

    #: What each capture mode costs and covers, said plainly at the point of
    #: choosing. "key"/"failure" leave gaps in the report, and a gap nobody was
    #: warned about reads as a bug.
    _SHOT_NOTES = {
        "all": "Every step is photographed. Roughly 4 MB per 20-step run.",
        "key": "Only verifications and steps that reach a new page. About a "
               "third of the storage; other steps will have no image.",
        "failure": "Every step is photographed, but only the failure and the "
                   "steps just before it are kept. Cheapest — a run that "
                   "passes stores nothing.",
        "off": "No images. The report still records each step's result and "
               "how long it took.",
    }

    def _shot_mode_changed(self, mode: str) -> None:
        self.shot_context.set_visibility(mode == "failure")
        self.shot_note.set_text(self._SHOT_NOTES.get(mode, ""))

    def _set_platform(self, p: str) -> None:
        self.platform = p
        self._wanted_targets = []           # another module offers other browsers
        self._render_left()
        self._render_right()
        if getattr(self, "config_select", None) is not None:
            ui.timer(0.02, lambda: self._load_configs(""), once=True)
        # _render_right rebuilds the panel from scratch, placeholder and all, so
        # the flow's fields have to be asked for again — otherwise switching
        # platform emptied the form while the flow stayed selected.
        if self.flow:
            ui.timer(0.01, lambda: self._load_inputs(self.flow), once=True)

    def _render_left(self) -> None:
        from nlp.platforms import RUN_TARGETS
        muted = f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}"
        head = f"font-weight:{TYPOGRAPHY['weight_bold']}"
        self.top.clear()
        self.left.clear()
        enabled = self._enabled()
        with self.top:
            # Like Testsigma's run dialog: pick a saved configuration first (it fills
            # everything below), and "Save as configuration" sits beside Run.
            with ui.row().classes("w-full items-center no-wrap gap-2").style("margin-bottom:6px"):
                self.config_select = ui.select({"": "— none —"}, value="",
                                               label="Saved configuration",
                                               on_change=lambda e: self._pick_config(e.value)) \
                    .props("outlined dense options-dense").style("width:24rem")
                self.config_del = ui.button(icon="delete_outline",
                                            on_click=lambda: self._delete_config()) \
                    .props("flat dense round color=negative").tooltip("Delete this configuration")
                self.config_del.set_visibility(False)
                self.config_note = ui.label("").style(muted)
            ui.timer(0.05, self._load_configs, once=True)
            with ui.row().classes("w-full items-start gap-3").style("flex-wrap:wrap"):
                self.platform_select = ui.select(
                    {p["name"]: p.get("label", p["name"]) for p in enabled},
                    value=self.platform if any(p["name"] == self.platform for p in enabled)
                    else (enabled[0]["name"] if enabled else None),
                    label="Platform", on_change=lambda e: self._set_platform(e.value),
                ).props("outlined dense").style("width:13rem")
                self.flow_select = ui.select(
                    self.projects or [], value=self.flow if self.flow in self.projects else None,
                    label="Test case", with_input=True,
                    on_change=lambda e: self._load_inputs(e.value),
                ).props("outlined dense").style("flex:1 1 18rem; min-width:16rem")
                with ui.column().classes("gap-0").style("flex:1 1 20rem; min-width:16rem"):
                    # Environment: the same test on live or a development host —
                    # every www.justdial.com URL moves there, with its saved login.
                    self.site_env = ui.select({"": "Default — URL as written in the test"},
                                              value="", label="Environment") \
                        .props("outlined dense").classes("w-full") \
                        .tooltip("Pick a server to run this test there without editing any URL "
                                 "— only servers for this platform are listed.")
                    self.site_env_hint = ui.label("").style(muted)
                self.run_btn = ui.button("Run now", icon="play_arrow", on_click=self.launch) \
                    .props("unelevated").style(
                        f"background:{COLORS['success']}; height:40px; padding:0 22px")
            if not self.projects:
                ui.label("No saved test cases yet — author one first.").style(muted)

            # Run on: one run per ticked browser, one after another.
            targets = RUN_TARGETS.get(self.platform, [])
            self.targets: list[str] = list(getattr(self, "_wanted_targets", []) or
                                           ([targets[0][0]] if targets else []))
            self._target_btns: dict[str, ui.button] = {}
            if targets:
                with ui.row().classes("w-full items-center gap-2").style("flex-wrap:wrap; margin-top:4px"):
                    ui.label("Run on").style(head + "; margin-right:4px")
                    for key, label in targets:
                        b = ui.button(label, on_click=lambda k=key: self._toggle_target(k)) \
                            .props("dense no-caps rounded").style("padding:2px 12px")
                        self._target_btns[key] = b
                    self.targets_note = ui.label("").style(muted)
                self._paint_targets()

            # Save as configuration (name it, optionally share) — saved when you
            # press Run, or straight away with "Save".
            with ui.row().classes("w-full items-center gap-3").style(
                    f"flex-wrap:wrap; margin-top:8px; padding-top:8px; border-top:1px solid {COLORS['border']}"):
                self.save_cfg = ui.checkbox("Save as configuration", value=False).props("dense")
                self.save_name = ui.input(placeholder="Configuration name, e.g. Live · iPhone + Android") \
                    .props("outlined dense").style("width:22rem")
                self.save_shared = ui.checkbox("Share with team", value=False).props("dense")
                save_now = ui.button("Save", icon="save", on_click=lambda: self._save_inline()) \
                    .props("flat dense no-caps")
                for w in (self.save_name, self.save_shared, save_now):
                    w.bind_visibility_from(self.save_cfg, "value")

        with self.left:
            ui.label("Run options").style(head)

            with ui.row().classes("w-full gap-x-6 gap-y-1").style("flex-wrap:wrap"):
                self.headless = ui.switch("Headless", value=False).props("dense") \
                    .tooltip("Off shows the browser while it runs.")
                self.stop_on_failure = ui.switch("Stop at the first failure", value=True) \
                    .props("dense").tooltip(
                        "On: a failed step ends the run, the rest are marked not run. Off: every "
                        "step is attempted — misleading for anything that submits a form.")
                self.record_video = ui.switch("Record video", value=False).props("dense") \
                    .tooltip("Saves a .webm under Reports → Video — for a re-run to attach to a ticket.")
            with ui.row().classes("w-full items-start no-wrap gap-2"):
                self.shot_mode = ui.select(
                    {"all": "Screenshot every step",
                     "key": "Only checks and new pages",
                     "failure": "Only the failure and the steps before it",
                     "off": "No screenshots"},
                    value="all", label="Screenshots",
                    on_change=lambda e: self._shot_mode_changed(e.value)) \
                    .props("outlined dense").classes("flex-grow")
                self.shot_context = ui.number(
                    "Steps before", value=5, min=0, max=50,
                    format="%d").props("outlined dense").style("width:7rem")
                self.shot_context.set_visibility(False)
            self.shot_note = ui.label(self._SHOT_NOTES["all"]).style(muted)

            with ui.expansion("Advanced", icon="tune").classes("w-full") \
                    .props("dense header-class=text-sm"):
                with ui.column().classes("w-full gap-2"):
                    mobile = [d for d in self.devices if d.get("mobile")]
                    with ui.row().classes("w-full no-wrap gap-2"):
                        self.device_select = ui.select(
                            {"": "From 'Run on'"} | {d["name"]: f"{d['name']}  ({d['viewport']})"
                                                    for d in mobile},
                            value="", label="Exact device").props("outlined dense").classes("flex-grow")
                        # The engine follows the device unless overridden.
                        self.browser_select = ui.select(
                            {"": "From 'Run on'", "chromium": "Chromium",
                             "firefox": "Firefox", "webkit": "WebKit / Safari"},
                            value="", label="Browser engine").props("outlined dense").classes("flex-grow")
                    ui.label("Only for a single browser in 'Run on' — overrides its device / engine.") \
                        .style(muted)
                    # Browser permission prompts are drawn by the BROWSER — decided here.
                    self.permissions = ui.select(
                        {"": "Ask the browser (default behaviour)",
                         "allow": "Allow all browser permissions",
                         "deny": "Deny all browser permissions"},
                        value="", label="Browser permission popups") \
                        .props("outlined dense").classes("w-full") \
                        .tooltip("Geolocation, notifications, camera, clipboard. Site popups stay "
                                 "under your test's control.")
                    # HTTP Basic login for a staging host (prot3, devx…).
                    self.auth_select = ui.select({"none": "Not needed / use URL login"},
                                                 value="none", label="Staging site login (HTTP Basic)") \
                        .props("outlined dense").classes("w-full")
                    self.auth_select.set_visibility(False)
                    self.auth_hint = ui.label("").style(muted)
                    self.auth_hint.set_visibility(False)
            self.identity = None            # replaced by "Run on"

    # ── Run on (browsers) ───────────────────────────────────────────────────
    def _toggle_target(self, key: str) -> None:
        if key in self.targets:
            if len(self.targets) == 1:
                ui.notify("Keep at least one browser", type="info")
                return
            self.targets.remove(key)
        else:
            self.targets.append(key)
        self._paint_targets()

    def _paint_targets(self) -> None:
        for key, b in getattr(self, "_target_btns", {}).items():
            on = key in self.targets
            b.props(f"unelevated color={'primary' if on else 'grey-4'} "
                    f"text-color={'white' if on else 'grey-9'} icon={'check' if on else 'none'}")
        n = len(self.targets)
        if getattr(self, "targets_note", None) is not None:
            self.targets_note.set_text(
                f"{n} runs, one after another — each with its own report" if n > 1 else "")
        if getattr(self, "run_btn", None) is not None:
            self.run_btn.set_text(f"Run on {n}" if n > 1 else "Run now")

    def _render_right(self) -> None:
        self.right.clear()
        with self.right:
            ui.label("Values this run needs").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            self.inputs_area = ui.column().classes("w-full gap-2")
            with self.inputs_area:
                ui.label("Pick a flow to see what it asks for.").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")

    async def _load_site_envs(self) -> None:
        sel = getattr(self, "site_env", None)
        if sel is None:
            return
        try:
            # Only the servers this platform runs on — staging2 / stg are Website
            # servers and are not offered for a Mobile Site run.
            envs = await api.site_environments(self.platform)
        except api.ApiError:
            envs = []
        opens = getattr(self, "_flow_opens", []) or []
        if opens:
            names = ", ".join(dict.fromkeys(o["name"] for o in opens))
            default_label = f"Default — {names} (as written in the test)"
        else:
            default_label = "Default — URL as written in the test"
        opts = {"": default_label}
        for e in envs:
            note = "" if not e.get("needs_login") or e.get("login_saved") else "  (login not saved — ask admin)"
            opts[e["name"]] = f"{e['name']} — {e['host']}{note}"
        want = getattr(self, "_wanted_env", "") or sel.value
        sel.set_options(opts, value=want if want in opts else "")

        def hint(_=None) -> None:
            v = sel.value or ""
            if not v:
                text = ("Every address is used exactly as written in the test"
                        + (f": {', '.join(o['host'] for o in opens)}." if opens else "."))
            elif v == "live":
                text = ("prot / prot3 / staging addresses in this test open on www.justdial.com "
                        "(the live site, no login).")
            else:
                text = (f"www.justdial.com and other test-environment links open on "
                        f"{opts[v].split(' — ')[1].split()[0]}; its login is attached automatically.")
            self.site_env_hint.set_text(text)
        sel.on_value_change(hint)
        hint()

    async def _load_auth_options(self, flow: str) -> None:
        """Offer the saved staging logins; preselect nothing (URL login stays default)."""
        sel = getattr(self, "auth_select", None)
        if sel is None:
            return
        try:
            info = await api.auth_domains(flow)
        except api.ApiError:
            info = {}
        hosts = info.get("hosts") or []
        self._flow_opens = info.get("opens") or []
        opts = {"none": "Not needed / use URL login"}
        opts.update({h: f"Attach saved login for {h}" for h in hosts})
        sug = info.get("suggested") or ""
        # Preselect the host the flow opens first. Credentials embedded in the
        # URL leave "user:pass@" in the page's own URL, and a page that builds
        # requests from it then fails with "Request cannot be constructed from a
        # URL that includes credentials" — the touch site stays on its spinner
        # for ever. Context-level login has none of that. It can still be
        # switched off here for a site that needs the URL form.
        # Default "" = let the server decide (the chosen Environment's login, or
        # the flow's own host); an explicit pick still overrides.
        opts = {"": "Automatic (recommended)", **opts}
        sel.set_options(opts, value="")
        sel.set_visibility(bool(hosts))
        self.auth_hint.set_visibility(bool(hosts))
        self.auth_hint.set_text(
            f"This flow opens {sug}, which has a saved login; it is attached to the "
            f"browser session so the page's own requests carry it too. Choose "
            f"'Not needed' only if this site loads better with the login in the URL."
            if sug else "Logins come from AUTH_<NAME>_DOMAIN / _USERNAME / _PASSWORD in .env.")

    async def _load_inputs(self, flow: str) -> None:
        """Read the flow and ask for every ${variable} it references."""
        self.flow = flow
        self.fields = {}
        self.inputs_area.clear()
        if not flow:
            return
        try:
            data = await api.get_project(flow)
        except api.ApiError as e:
            with self.inputs_area:
                ui.label(f"Could not read {flow}: {e.detail}").style(
                    f"color:{COLORS['danger']}")
            return

        try:
            store = await api.testdata("", self.platform)
            self.provided = store.get("values", {}) or {}
        except api.ApiError:
            self.provided = {}
        await self._load_auth_options(flow)
        await self._load_site_envs()

        # Only what the flow needs from OUTSIDE: every ${var} referenced minus
        # the ones an earlier step of the same flow produces (`store … as x`,
        # `api get … as x`, `store json … as x`). Listing every reference asked
        # the operator for values the run itself was about to compute, and
        # then refused to start until they were typed.
        from ai_flow_builder.emitter import run_parameters
        names: list[str] = run_parameters([
            ln for ln in data.get("steps", [])
            if not ln.strip().startswith("#")])

        with self.inputs_area:
            if not names:
                ui.label("This flow needs no values.").style(
                    f"font-size:{TYPOGRAPHY['size_sm']}; color:{COLORS['text_muted']}")
                return
            saved = [n for n in names if self.provided.get(n, {}).get("defined")]
            ui.label(f"{len(names)} value(s), {len(saved)} already saved under "
                     f"Test Data. Anything you type here is used for this run "
                     f"only and is not stored.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            for n in names:
                secret = _is_secret(n)
                have = self.provided.get(n, {})
                on_file = bool(have.get("defined"))
                box = ui.input(label=n, password=secret,
                               password_toggle_button=secret,
                               placeholder=(f"saved: {have.get('display','')}"
                                            if on_file else "")) \
                    .props("outlined dense").classes("w-full")
                if on_file:
                    # Left EMPTY on purpose: empty means "use what is saved".
                    # Pre-filling it would send the stored value back as a
                    # per-run override, which looks identical until the saved
                    # value changes and the run keeps using the old one.
                    ui.label(f"✓ comes from Test Data ({have.get('display','')})"
                             f" — leave blank to use it, or type to override "
                             f"for this run only").style(
                        f"font-size:{TYPOGRAPHY['size_xs']};"
                        f"color:{COLORS['success']}; margin-top:-6px")
                elif secret:
                    box.tooltip("Treated as a secret: masked here, and never "
                                "written to the log or echoed by the API")
                self.fields[n] = box

    # ── saved configurations ────────────────────────────────────────────────
    async def _load_configs(self, select: str = "") -> None:
        try:
            self._configs = await api.run_configs(self.platform)
        except api.ApiError:
            self._configs = []
        opts = {"": "— none (set options below) —"}
        for c in self._configs:
            opts[c["id"]] = c["name"] + ("" if c.get("mine") else f"  (shared by {c.get('owner')})") \
                + ("  · shared" if c.get("mine") and c.get("shared") else "")
        want = select or getattr(self, "_wanted_config", "") or ""
        self.config_select.set_options(opts, value=want if want in opts else "")
        if want and want in opts:
            self._pick_config(want)

    def _settings_now(self) -> dict:
        return {"headless": bool(self.headless.value),
                "device_name": self.device_select.value or "",
                "browser": self.browser_select.value or "",
                "targets": list(getattr(self, "targets", []) or []),
                "site_env": (getattr(self, "site_env", None) and self.site_env.value) or "",
                "http_auth_domain": (getattr(self, "auth_select", None) and self.auth_select.value) or "",
                "browser_permissions": self.permissions.value or "",
                "screenshot_mode": self.shot_mode.value or "all",
                "screenshot_context": int(self.shot_context.value or 5),
                "stop_on_failure": bool(self.stop_on_failure.value),
                "record_video": bool(getattr(self, "record_video", None) and self.record_video.value)}

    def _pick_config(self, cid: str) -> None:
        c = next((x for x in getattr(self, "_configs", []) if x["id"] == cid), None)
        self.config_del.set_visibility(bool(c and c.get("mine")))
        if not c:
            self.config_note.set_text("")
            return
        s = c.get("settings") or {}

        def put(widget, value) -> None:
            if widget is None or value is None:
                return
            opts = getattr(widget, "options", None)
            if isinstance(opts, dict) and value not in opts and value != "":
                if widget is getattr(self, "site_env", None):
                    self._wanted_env = value        # list may still be loading
                return
            widget.set_value(value)
        put(self.headless, s.get("headless"))
        put(self.shot_mode, s.get("screenshot_mode"))
        put(self.shot_context, s.get("screenshot_context"))
        put(self.stop_on_failure, s.get("stop_on_failure"))
        put(self.device_select, s.get("device_name"))
        put(self.browser_select, s.get("browser"))
        put(getattr(self, "record_video", None), s.get("record_video"))
        put(self.permissions, s.get("browser_permissions"))
        from nlp.platforms import RUN_TARGETS
        valid = [k for k, _ in RUN_TARGETS.get(self.platform, [])]
        tg = [t for t in (s.get("targets") or ([s["browser_identity"]] if s.get("browser_identity") else []))
              if t in valid]
        if tg:
            self.targets = tg
            self._paint_targets()
        put(getattr(self, "site_env", None), s.get("site_env"))
        put(getattr(self, "auth_select", None), s.get("http_auth_domain"))
        self.config_note.set_text(c.get("summary", ""))
        if c.get("mine") and getattr(self, "save_name", None) is not None:
            self.save_name.set_value(c["name"])
            self.save_shared.set_value(bool(c.get("shared")))

    def _save_config_dialog(self) -> None:
        cur = next((x for x in getattr(self, "_configs", [])
                    if x["id"] == (self.config_select.value or "") and x.get("mine")), None)
        dialog = ui.dialog()
        with dialog, ui.card().style("width:28rem"):
            ui.label("Save run configuration").style(
                f"font-size:{TYPOGRAPHY['size_lg']}; font-weight:{TYPOGRAPHY['weight_bold']}")
            name = ui.input("Name", value=(cur or {}).get("name", ""),
                            placeholder="Live · Samsung Internet · headless") \
                .props("outlined dense autofocus").classes("w-full")
            shared = ui.checkbox("Share with the team", value=bool((cur or {}).get("shared")))
            ui.label(f"Saved for {self.platform} — the options below, not test values "
                     "(those stay in Test Data). Shared: everyone can use it; only you can change it.") \
                .style(f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            async def go(update: bool) -> None:
                try:
                    res = await api.save_run_config((name.value or "").strip(), self.platform,
                                                    self._settings_now(), bool(shared.value),
                                                    cur["id"] if (update and cur) else "")
                except api.ApiError as e:
                    ui.notify(str(e.detail), type="negative")
                    return
                dialog.close()
                ui.notify(f"Saved '{res['name']}'", type="positive")
                await self._load_configs(res["id"])
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                if cur:
                    ui.button("Save as new", on_click=lambda: go(False)).props("flat")
                    ui.button("Update", icon="save", on_click=lambda: go(True)).props("unelevated")
                else:
                    ui.button("Save", icon="save", on_click=lambda: go(False)).props("unelevated")
        dialog.open()

    async def _save_inline(self, quiet: bool = False) -> bool:
        name = (self.save_name.value or "").strip()
        if not name:
            ui.notify("Give the configuration a name", type="warning")
            return False
        cur = next((x for x in getattr(self, "_configs", [])
                    if x.get("mine") and x["name"].lower() == name.lower()), None)
        try:
            res = await api.save_run_config(name, self.platform, self._settings_now(),
                                            bool(self.save_shared.value), cur["id"] if cur else "")
        except api.ApiError as e:
            ui.notify(str(e.detail), type="negative")
            return False
        ui.notify(("Updated" if cur else "Saved") + f" configuration '{res['name']}'", type="positive")
        self.save_cfg.set_value(False)
        await self._load_configs(res["id"])
        return True

    async def _delete_config(self) -> None:
        cid = self.config_select.value or ""
        if not cid:
            return
        try:
            await api.delete_run_config(cid)
        except api.ApiError as e:
            ui.notify(str(e.detail), type="negative")
            return
        ui.notify("Configuration deleted", type="positive")
        await self._load_configs("")

    async def launch(self) -> None:
        if not self.flow:
            ui.notify("Pick a flow first", type="warning")
            return
        if getattr(self, "_launching", False):
            return                  # a double-click used to start two browsers
        self._launching = True
        try:
            await self._launch()
        finally:
            self._launching = False

    async def _launch(self) -> None:
        if getattr(self, "save_cfg", None) is not None and self.save_cfg.value:
            if not await self._save_inline():
                return
        typed = {n: (b.value or "").strip() for n, b in self.fields.items()}
        # Only what was actually typed travels as a per-run value. A blank box
        # for a name Test Data supplies means "use the saved one", and sending
        # "" instead would overwrite it with nothing.
        params = {n: v for n, v in typed.items() if v}
        missing = [n for n, v in typed.items()
                   if not v and not self.provided.get(n, {}).get("defined")]
        if missing:
            ui.notify(f"Still needed: {', '.join(missing)} — type them here, or "
                      f"save them under Test Data so every run picks them up",
                      type="warning", timeout=7000)
            return
        targets = list(getattr(self, "targets", []) or [""])
        single = len(targets) == 1
        run_ids: list[str] = []
        for ident in targets:
            try:
                res = await api.run(
                    self.flow, self.platform,
                    headless=bool(self.headless.value),
                    # Exact device / engine overrides apply to a single browser only.
                    device_name=(self.device_select.value or "") if single else "",
                    browser=(self.browser_select.value or "") if single else "",
                    parameters=params,
                    secret_parameters=[n for n in params if _is_secret(n)],
                    browser_permissions=self.permissions.value or "",
                    stop_on_failure=bool(self.stop_on_failure.value),
                    screenshot_mode=self.shot_mode.value or "all",
                    screenshot_context=int(self.shot_context.value or 5),
                    http_auth_domain=(getattr(self, "auth_select", None) and self.auth_select.value) or "",
                    record_video=bool(getattr(self, "record_video", None) and self.record_video.value),
                    browser_identity=ident,
                    site_env=(getattr(self, "site_env", None) and self.site_env.value) or "",
                )
            except api.ApiError as e:
                ui.notify(f"Could not start ({ident or 'default'}): {e.detail}", type="negative")
                continue
            run_ids.append(f"{res['run_id']}:{ident}")
        if not run_ids:
            return
        first = run_ids[0].split(":")[0]
        batch = f"&batch={','.join(run_ids)}" if len(run_ids) > 1 else ""
        ui.navigate.to(f"/run/live?run_id={first}&flow={self.flow}"
                       f"&platform={self.platform}{batch}")


async def render(flow: str = "", platform: str = "website", *,
                 device: str = "", browser: str = "", identity: str = "", env: str = "",
                 config: str = "", autorun: bool = False) -> None:
    page = RunCenter(flow, platform)
    page._wanted_config = config
    await page.load()
    page.render()
    if device and getattr(page, "device_select", None) is not None:
        page.device_select.set_value(device)
    if browser and getattr(page, "browser_select", None) is not None:
        page.browser_select.set_value(browser)
    if identity and identity in getattr(page, "_target_btns", {}):
        page.targets = [identity]          # a report's Re-run: the browser it ran on
        page._paint_targets()
    page._wanted_env = env or ""
    # Setting a select's initial `value` does not fire its on_change, so a flow
    # arriving in the URL — which is how the editor's Run button gets here —
    # filled the dropdown and nothing else. The panel kept saying "Pick a flow
    # to see what it asks for" and Run Now failed asking for values there was
    # nowhere to type. Ask for them explicitly.
    if flow and flow in page.projects:
        await page._load_inputs(flow)
    # Quick run with a saved configuration: apply it, then start — unless the
    # test still needs a value typed here (then the form stays open for it).
    if config and autorun and flow:
        async def _go() -> None:
            await page._load_configs(config)
            await page.launch()
        ui.timer(1.2, _go, once=True)
