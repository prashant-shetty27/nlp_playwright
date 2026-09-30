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
        with ui.row().classes("w-full items-center gap-3").style("padding:8px 16px 0"):
            _back_to_test_case(self.flow, self.platform)
        with ui.row().classes("w-full no-wrap gap-4 p-4"):
            self.left = ui.column().classes("gap-3").style("width:26rem; flex:none")
            self.right = ui.column().classes("flex-grow gap-3")
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
        self._render_right()
        # _render_right rebuilds the panel from scratch, placeholder and all, so
        # the flow's fields have to be asked for again — otherwise switching
        # platform emptied the form while the flow stayed selected.
        if self.flow:
            ui.timer(0.01, lambda: self._load_inputs(self.flow), once=True)

    def _render_left(self) -> None:
        self.left.clear()
        with self.left:
            ui.label("What to run").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            enabled = self._enabled()
            self.platform_select = ui.select(
                {p["name"]: p.get("label", p["name"]) for p in enabled},
                value=self.platform if any(p["name"] == self.platform for p in enabled)
                else (enabled[0]["name"] if enabled else None),
                label="Platform", on_change=lambda e: self._set_platform(e.value),
            ).props("outlined dense").classes("w-full")

            self.flow_select = ui.select(
                self.projects or [], value=self.flow if self.flow in self.projects else None,
                label="Flow", with_input=True,
                on_change=lambda e: self._load_inputs(e.value),
            ).props("outlined dense").classes("w-full")
            if not self.projects:
                ui.label("No saved flows yet — author one first.").style(
                    f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            ui.separator()
            ui.label("How to run it").style(
                f"font-weight:{TYPOGRAPHY['weight_bold']}")
            self.headless = ui.switch("Headless", value=False).props("dense")
            ui.label("Off shows the browser while it runs.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            # Screenshots per step. What makes a failed run readable afterwards
            # — and the mode is what keeps it affordable: every step on a
            # 20-step run is about 4 MB, so fifty runs a day is real storage.
            self.shot_mode = ui.select(
                {"all": "Screenshot every step",
                 "key": "Only checks and new pages",
                 "failure": "Only the failure and the steps before it",
                 "off": "No screenshots"},
                value="all", label="Screenshots",
                on_change=lambda e: self._shot_mode_changed(e.value)) \
                .props("outlined dense").classes("w-full")
            self.shot_context = ui.number(
                "Steps to keep before the failure", value=5, min=0, max=50,
                format="%d").props("outlined dense").classes("w-full")
            self.shot_context.set_visibility(False)
            self.shot_note = ui.label(
                "Every step is photographed. Roughly 4 MB per 20-step run.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            self.stop_on_failure = ui.switch("Stop at the first failure",
                                             value=True).props("dense")
            ui.label("On, a failed step ends the run and the rest are marked not "
                     "run. Off, every step is attempted — useful for seeing how "
                     "much of a flow is broken at once, misleading for anything "
                     "that submits a form.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            mobile = [d for d in self.devices if d.get("mobile")]
            self.device_select = ui.select(
                {"": "Platform default"} | {d["name"]: f"{d['name']}  ({d['viewport']})"
                                            for d in mobile},
                value="", label="Device").props("outlined dense").classes("w-full")
            # The engine follows the device unless overridden, so the default is
            # named rather than left blank — an operator should not have to know
            # that iPhone implies WebKit.
            self.browser_select = ui.select(
                {"": "From device (recommended)", "chromium": "Chromium",
                 "firefox": "Firefox", "webkit": "WebKit / Safari"},
                value="", label="Browser engine").props("outlined dense").classes("w-full")

            # A .webm of the whole session, for attaching to a ticket. Off by
            # default: it is a few MB per minute and slows the browser slightly,
            # so it is switched on for the confirming re-run, not every run.
            self.record_video = ui.switch("Record video", value=False).props("dense")
            ui.label("Saves a .webm of the run under Reports → Video. Turn on for a "
                     "re-run whose recording you want to attach to a ticket.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            # Browser permission prompts (geolocation, notifications, camera…)
            # are drawn by the BROWSER, so no locator can reach them and a run
            # simply stalls behind one. Decided here, before the browser opens.
            self.permissions = ui.select(
                {"": "Ask the browser (default behaviour)",
                 "allow": "Allow all browser permissions",
                 "deny": "Deny all browser permissions"},
                value="", label="Browser permission popups") \
                .props("outlined dense").classes("w-full")
            ui.label("Geolocation, notifications, camera, clipboard. Site popups "
                     "— cookie banners, login modals — are page content and stay "
                     "under your test's control.").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            # Environment: run the same test case on live or on a development /
            # pre-prod host. Every www.justdial.com URL it opens (typed or from
            # Test Data) moves to that host, and that host's saved login is attached.
            self.site_env = ui.select({"": "Live — URLs as written (www.justdial.com)"},
                                      value="", label="Environment") \
                .props("outlined dense").classes("w-full") \
                .tooltip("Pick prot / prot3 / devx … to run this test there without editing any URL.")
            self.site_env_hint = ui.label("").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")

            # Which browser the site should see. Samsung Internet and the stock
            # browsers are Chromium; the site tells them apart by user agent.
            self.identity = ui.select(
                {"": "Device default (Chrome on Android / Safari on iPhone)",
                 "samsung_internet": "Samsung Internet (Galaxy S9+, Chromium)",
                 "ios_safari": "Safari on iPhone (iPhone 15, WebKit)",
                 "ios_chrome": "Chrome on iPhone (iPhone 15, WebKit engine)",
                 "android_chrome": "Chrome on Android (Pixel 7)"},
                value="", label="Browser identity") \
                .props("outlined dense").classes("w-full") \
                .tooltip("Sets the user agent (and the matching device/engine unless you chose "
                         "them above). Use it to check a design change on Safari or Samsung Internet.")

            # HTTP Basic login for a staging host (prot3, devx…). Off by default:
            # the browser then answers the server's challenge with the URL-embedded
            # credentials, which is what every existing flow relies on. "On"
            # attaches the saved login to the browser context instead, so XHR
            # calls the page makes after loading carry it too.
            self.auth_select = ui.select({"none": "Not needed / use URL login"},
                                         value="none", label="Staging site login (HTTP Basic)") \
                .props("outlined dense").classes("w-full")
            self.auth_select.set_visibility(False)
            self.auth_hint = ui.label("").style(
                f"font-size:{TYPOGRAPHY['size_xs']}; color:{COLORS['text_muted']}")
            self.auth_hint.set_visibility(False)

            ui.button("Run Now", icon="play_arrow", on_click=self.launch) \
                .props("unelevated").classes("w-full") \
                .style(f"background:{COLORS['success']}; margin-top:8px")

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
            envs = await api.site_environments()
        except api.ApiError:
            envs = []
        opts = {"": "Live — URLs as written (www.justdial.com)"}
        for e in envs:
            note = "" if not e.get("needs_login") or e.get("login_saved") else "  (login not saved — ask admin)"
            opts[e["name"]] = f"{e['name']} — {e['host']}{note}"
        want = getattr(self, "_wanted_env", "") or sel.value
        sel.set_options(opts, value=want if want in opts else "")

        def hint(_=None) -> None:
            v = sel.value or ""
            self.site_env_hint.set_text(
                "" if not v else f"www.justdial.com links in this test open on {opts[v].split(' — ')[1].split()[0]}; "
                                 "its login is attached automatically.")
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
            store = await api.testdata("")
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
        try:
            res = await api.run(
                self.flow, self.platform,
                headless=bool(self.headless.value),
                device_name=self.device_select.value or "",
                browser=self.browser_select.value or "",
                parameters=params,
                secret_parameters=[n for n in params if _is_secret(n)],
                browser_permissions=self.permissions.value or "",
                stop_on_failure=bool(self.stop_on_failure.value),
                screenshot_mode=self.shot_mode.value or "all",
                screenshot_context=int(self.shot_context.value or 5),
                http_auth_domain=(getattr(self, "auth_select", None) and self.auth_select.value) or "",
                record_video=bool(getattr(self, "record_video", None) and self.record_video.value),
                browser_identity=(getattr(self, "identity", None) and self.identity.value) or "",
                site_env=(getattr(self, "site_env", None) and self.site_env.value) or "",
            )
        except api.ApiError as e:
            ui.notify(f"Could not start: {e.detail}", type="negative")
            return
        ui.navigate.to(f"/run/live?run_id={res['run_id']}&flow={self.flow}"
                       f"&platform={self.platform}")


async def render(flow: str = "", platform: str = "website", *,
                 device: str = "", browser: str = "", identity: str = "", env: str = "") -> None:
    page = RunCenter(flow, platform)
    await page.load()
    page.render()
    if device and getattr(page, "device_select", None) is not None:
        page.device_select.set_value(device)
    if browser and getattr(page, "browser_select", None) is not None:
        page.browser_select.set_value(browser)
    if identity and getattr(page, "identity", None) is not None:
        page.identity.set_value(identity)
    page._wanted_env = env or ""
    # Setting a select's initial `value` does not fire its on_change, so a flow
    # arriving in the URL — which is how the editor's Run button gets here —
    # filled the dropdown and nothing else. The panel kept saying "Pick a flow
    # to see what it asks for" and Run Now failed asking for values there was
    # nowhere to type. Ask for them explicitly.
    if flow and flow in page.projects:
        await page._load_inputs(flow)
