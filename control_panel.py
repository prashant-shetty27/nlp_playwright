"""
control_panel.py — Master Control Panel for NLP Playwright Automation

One-stop dashboard to:
  • Start / stop all servers with a single button (Web, Android, iOS, Appium, Spy)
  • Keep all recorders always active
  • Run any flow directly from the UI with live output

Usage:
    python control_panel.py
    python control_panel.py --port 9000
"""

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from nicegui import app, ui

# ── Paths ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
_venv_py = BASE_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
PYTHON = str(_venv_py) if _venv_py.exists() else sys.executable

LOG_DIR = BASE_DIR / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Server Definitions ──────────────────────────────────────────────────────

SERVERS = [
    {
        # The portal itself — the Test Cases editor, Run Center, Test Data and
        # reports. It was the one service NOT managed here, so it died with
        # whatever terminal started it and had to be relaunched by hand, on
        # whichever port happened to be free. That is why its URL kept moving.
        #
        # 8100 is reserved for it deliberately: the recorders already own
        # 8080/8090/8091 and take them at boot, so anything in that range gets
        # claimed out from under the portal on the next restart.
        "id":       "portal",
        "name":     "Codeless Automation",
        "icon":     "dashboard",
        "port":     8100,
        "color":    "indigo",
        "cmd":      [PYTHON, "-m", "ui.app", "--port", "8100"],
        "log":      str(LOG_DIR / "portal.log"),
        "pid_file": "/tmp/nlp_portal.pid",
        "url":      "http://localhost:8100",
        "desc":     "Test cases, Run Center, Test Data, reports",
    },
    {
        "id":       "web_recorder",
        "name":     "Web Recorder",
        "icon":     "language",
        "port":     8080,
        "color":    "blue",
        "cmd":      [PYTHON, str(BASE_DIR / "ui_builder.py")],
        "log":      str(LOG_DIR / "web_recorder.log"),
        "pid_file": "/tmp/nlp_web_recorder.pid",
        "url":      "http://localhost:8080",
        "desc":     "Visual web element recorder (Playwright)",
    },
    {
        "id":       "android_recorder",
        "name":     "Android Recorder",
        "icon":     "android",
        "port":     8090,
        "color":    "green",
        "cmd":      [PYTHON, str(BASE_DIR / "recorder_ui.py"),
                     "--platform", "android",
                     "--caps", str(BASE_DIR / "suites" / "android_suite.json"),
                     "--port", "8090"],
        "log":      str(LOG_DIR / "android_recorder.log"),
        "pid_file": "/tmp/nlp_android_recorder.pid",
        "url":      "http://localhost:8090",
        "desc":     "Visual Android element recorder (Appium UiAutomator2)",
    },
    {
        "id":       "ios_recorder",
        "name":     "iOS Recorder",
        "icon":     "phone_iphone",
        "port":     8091,
        "color":    "purple",
        "cmd":      [PYTHON, str(BASE_DIR / "recorder_ui.py"),
                     "--platform", "ios",
                     "--caps", str(BASE_DIR / "suites" / "ios_suite.json"),
                     "--port", "8091"],
        "log":      str(LOG_DIR / "ios_recorder.log"),
        "pid_file": "/tmp/nlp_ios_recorder.pid",
        "url":      "http://localhost:8091",
        "desc":     "Visual iOS element recorder (Appium XCUITest)",
    },
    {
        "id":       "appium",
        "name":     "Appium Server",
        "icon":     "devices",
        "port":     4723,
        "color":    "orange",
        "cmd":      ["appium", "--port", "4723",
                     "--log", str(LOG_DIR / "appium.log"),
                     "--log-level", "info"],
        "log":      str(LOG_DIR / "appium.log"),
        "pid_file": "/tmp/nlp_appium.pid",
        "url":      "http://localhost:4723",
        "desc":     "Appium automation server (Android + iOS devices)",
    },
    {
        "id":       "spy_server",
        "name":     "Spy Server",
        "icon":     "visibility",
        "port":     5050,
        "color":    "teal",
        "cmd":      [PYTHON, str(BASE_DIR / "spy" / "server.py")],
        "log":      str(LOG_DIR / "spy_server.log"),
        "pid_file": "/tmp/nlp_spy_server.pid",
        "url":      "http://localhost:5050",
        "desc":     "Chrome extension backend (Alt+Click element spy)",
    },
]

# ── Process Management ──────────────────────────────────────────────────────

def is_port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def start_server(server: dict) -> bool:
    if is_port_open(server["port"]):
        return True
    try:
        log_fh = open(server["log"], "a")
        proc = subprocess.Popen(
            server["cmd"],
            cwd=str(BASE_DIR),
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
        )
        with open(server["pid_file"], "w") as f:
            f.write(str(proc.pid))
        return True
    except Exception as e:
        print(f"[control_panel] Failed to start {server['name']}: {e}")
        return False


def stop_server(server: dict) -> None:
    try:
        pid_file = server["pid_file"]
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = int(f.read().strip())
            os.kill(pid, 15)
            os.remove(pid_file)
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{server['port']}"],
            capture_output=True, text=True,
        )
        for pid in result.stdout.strip().split():
            if pid:
                subprocess.run(["kill", "-9", pid], capture_output=True)
    except Exception:
        pass


def start_all_servers() -> None:
    for server in SERVERS:
        start_server(server)


def stop_all_servers() -> None:
    for server in SERVERS:
        stop_server(server)


# ── Flow Discovery ──────────────────────────────────────────────────────────

def scan_flows() -> dict:
    """Return flows grouped by platform."""
    groups: dict[str, list[str]] = {"web": [], "android": [], "ios": []}
    flows_dir = BASE_DIR / "flows"
    if not flows_dir.exists():
        return groups

    for fpath in sorted(flows_dir.rglob("*.flow")):
        rel = str(fpath.relative_to(BASE_DIR))
        name = fpath.stem.lower()
        parent = fpath.parent.name.lower()
        if parent in ("android",) or name.startswith("android"):
            groups["android"].append(rel)
        elif parent in ("ios",) or name.startswith("ios"):
            groups["ios"].append(rel)
        else:
            groups["web"].append(rel)

    return groups


# ── UI State (per page visit; single-user panel) ────────────────────────────

_ui: dict = {
    "dots":       {},   # server_id → ui.icon
    "labels":     {},   # server_id → ui.label
    "start_btns": {},   # server_id → ui.button
    "stop_btns":  {},   # server_id → ui.button
}


def _apply_status(sid: str, running: bool) -> None:
    dot = _ui["dots"].get(sid)
    lbl = _ui["labels"].get(sid)
    if dot is not None:
        dot.classes(remove="text-gray-400 text-green-500 text-red-500",
                    add=f"text-{'green' if running else 'red'}-500")
        dot.props(f'name={"circle" if running else "remove_circle"}')
    if lbl is not None:
        lbl.set_text("Running" if running else "Stopped")
        lbl.classes(remove="text-green-600 text-red-600 text-gray-400",
                    add=f"text-{'green' if running else 'red'}-600")


def refresh_all_status() -> None:
    for server in SERVERS:
        _apply_status(server["id"], is_port_open(server["port"]))


def _handle_start(server: dict) -> None:
    ui.notify(f"Starting {server['name']}...", type="info")
    start_server(server)
    time.sleep(1.2)
    _apply_status(server["id"], is_port_open(server["port"]))


def _handle_stop(server: dict) -> None:
    ui.notify(f"Stopping {server['name']}...", type="warning")
    stop_server(server)
    time.sleep(0.5)
    _apply_status(server["id"], is_port_open(server["port"]))


# ── Flow Runner ─────────────────────────────────────────────────────────────

async def run_flow(platform: str, flow_file: str, log_el) -> None:
    if not flow_file:
        ui.notify("Select a flow file first", type="warning")
        return

    log_el.push(f"{'='*60}")
    log_el.push(f"Platform : {platform.upper()}")
    log_el.push(f"Flow     : {flow_file}")
    log_el.push(f"{'='*60}")

    if platform == "web":
        cmd = [PYTHON, str(BASE_DIR / "runner.py"), str(BASE_DIR / flow_file)]
    else:
        cmd = [PYTHON, str(BASE_DIR / "runner_appium.py"),
               str(BASE_DIR / flow_file), "--platform", platform]

    log_el.push(f"$ {' '.join(cmd)}\n")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(BASE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for raw in proc.stdout:
            log_el.push(raw.decode("utf-8", errors="replace").rstrip())
        await proc.wait()
        ok = proc.returncode == 0
        log_el.push(f"\n{'='*60}")
        log_el.push(f"Result: {'PASSED' if ok else f'FAILED (exit {proc.returncode})'}")
        ui.notify("Flow passed" if ok else "Flow failed",
                  type="positive" if ok else "negative")
    except Exception as exc:
        log_el.push(f"ERROR: {exc}")
        ui.notify(str(exc), type="negative")


# ── Page ────────────────────────────────────────────────────────────────────

CARD_COLORS = {
    "blue":   "border-blue-500",
    "green":  "border-green-500",
    "purple": "border-purple-500",
    "orange": "border-orange-500",
    "teal":   "border-teal-500",
}

ICON_COLORS = {
    "blue":   "text-blue-500",
    "green":  "text-green-500",
    "purple": "text-purple-500",
    "orange": "text-orange-500",
    "teal":   "text-teal-500",
}


@ui.page("/")
def main_page():
    ui.add_head_html('<link rel="preconnect" href="https://fonts.googleapis.com">')

    # ── Header ────────────────────────────────────────────────────────────
    with ui.header().classes(
        "bg-gray-900 text-white flex items-center justify-between px-6 py-3 shadow-lg"
    ):
        with ui.row().classes("items-center gap-3"):
            ui.icon("smart_toy", size="2rem").classes("text-blue-400")
            ui.label("NLP Playwright — Control Panel").classes(
                "text-xl font-bold tracking-wide"
            )

        with ui.row().classes("gap-3"):
            ui.button(
                "Start All Servers",
                icon="play_circle",
                on_click=lambda: (
                    start_all_servers(),
                    ui.notify("Starting all servers…", type="positive"),
                    ui.timer(1.5, callback=refresh_all_status, once=True),
                ),
            ).classes("bg-green-600 hover:bg-green-700 text-white font-semibold")

            ui.button(
                "Stop All",
                icon="stop_circle",
                on_click=lambda: (
                    stop_all_servers(),
                    ui.notify("Stopping all servers…", type="warning"),
                    ui.timer(0.8, callback=refresh_all_status, once=True),
                ),
            ).classes("bg-red-600 hover:bg-red-700 text-white font-semibold")

    # ── Servers Section ───────────────────────────────────────────────────
    with ui.column().classes("w-full max-w-7xl mx-auto px-4 mt-6"):

        ui.label("Servers").classes("text-base font-bold text-gray-500 uppercase tracking-widest mb-3")

        with ui.grid(columns=3).classes("gap-4 w-full"):
            for server in SERVERS:
                sid = server["id"]
                border = CARD_COLORS.get(server["color"], "border-gray-400")
                icon_cls = ICON_COLORS.get(server["color"], "text-gray-500")

                with ui.card().classes(
                    f"w-full border-l-4 {border} rounded-xl shadow hover:shadow-md transition-shadow"
                ):
                    # Title row
                    with ui.row().classes("items-center gap-2 mb-1"):
                        ui.icon(server["icon"], size="1.4rem").classes(icon_cls)
                        ui.label(server["name"]).classes("font-semibold text-gray-800 text-sm flex-1")
                        dot = ui.icon("circle", size="0.75rem").classes("text-gray-400")
                        _ui["dots"][sid] = dot

                    # Description
                    ui.label(server["desc"]).classes("text-xs text-gray-500 leading-tight mb-1")
                    ui.label(f"localhost:{server['port']}").classes(
                        "text-xs text-gray-400 font-mono"
                    )

                    # Status label
                    lbl = ui.label("Checking…").classes("text-xs font-semibold mt-1 text-gray-400")
                    _ui["labels"][sid] = lbl

                    # Action buttons
                    with ui.row().classes("gap-2 mt-3"):
                        ui.button(
                            "Start",
                            icon="play_arrow",
                            on_click=lambda s=server: _handle_start(s),
                        ).classes(
                            "text-xs px-3 py-1 bg-green-600 hover:bg-green-700 text-white rounded"
                        ).props("flat dense")

                        ui.button(
                            "Stop",
                            icon="stop",
                            on_click=lambda s=server: _handle_stop(s),
                        ).classes(
                            "text-xs px-3 py-1 bg-red-600 hover:bg-red-700 text-white rounded"
                        ).props("flat dense")

                        ui.button(
                            "Open",
                            icon="open_in_new",
                            on_click=lambda s=server: ui.navigate.to(s["url"], new_tab=True),
                        ).classes(
                            "text-xs px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded"
                        ).props("flat dense")

        # ── Flow Runner ───────────────────────────────────────────────────
        ui.separator().classes("my-6")
        ui.label("Flow Runner").classes(
            "text-base font-bold text-gray-500 uppercase tracking-widest mb-3"
        )

        all_flows = scan_flows()

        with ui.card().classes("w-full rounded-xl shadow"):
            with ui.row().classes("gap-4 items-end flex-wrap"):
                platform_sel = ui.select(
                    ["web", "android", "ios"],
                    label="Platform",
                    value="web",
                ).classes("w-36")

                flow_sel = ui.select(
                    all_flows.get("web", []),
                    label="Flow File",
                    value=all_flows["web"][0] if all_flows.get("web") else "",
                ).classes("min-w-64 flex-1")

                def _on_platform(e):
                    opts = all_flows.get(e.value, [])
                    flow_sel.options = opts
                    flow_sel.value = opts[0] if opts else ""
                    flow_sel.update()

                platform_sel.on("update:model-value", _on_platform)

                log_box = ui.log(max_lines=300).classes(
                    "w-full h-72 mt-4 font-mono text-xs bg-gray-950 text-green-400 rounded"
                )

                ui.button(
                    "Run Now",
                    icon="play_arrow",
                    on_click=lambda: asyncio.ensure_future(
                        run_flow(platform_sel.value, flow_sel.value, log_box)
                    ),
                ).classes("bg-green-600 hover:bg-green-700 text-white font-semibold")

                ui.button(
                    "Clear Log",
                    icon="clear",
                    on_click=lambda: log_box.clear(),
                ).classes("bg-gray-600 text-white").props("flat")

    # ── Footer ────────────────────────────────────────────────────────────
    with ui.footer().classes("bg-gray-100 text-gray-500 text-xs text-center py-2"):
        ui.label("NLP Playwright Control Panel  •  Auto-refresh every 5 s")

    # ── Status Polling ─────────────────────────────────────────────────────
    refresh_all_status()
    ui.timer(5.0, callback=refresh_all_status)


# ── Auto-start on launch ────────────────────────────────────────────────────

@app.on_startup
async def on_startup():
    """Start all servers automatically when the control panel boots."""
    print("[control_panel] Auto-starting all servers…")
    await asyncio.get_running_loop().run_in_executor(None, start_all_servers)
    print("[control_panel] All servers launched.")


# ── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NLP Playwright Control Panel")
    parser.add_argument("--port", type=int, default=8000, help="Control panel port (default 8000)")
    parser.add_argument("--no-auto-start", action="store_true",
                        help="Do not auto-start servers on launch")
    args = parser.parse_args()

    if args.no_auto_start:
        app.on_startup_handlers.clear()

    ui.run(
        host="0.0.0.0",
        port=args.port,
        title="NLP Playwright Control Panel",
        favicon="🤖",
        dark=False,
        reload=False,
        show=True,
    )
