import fcntl
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from nicegui import app, ui, Client, run

from config.settings import RECORDED_ELEMENTS_FILE, MANUAL_LOCATORS_FILE
from core import recording_context as rc
from core import recorder_security as rs

# ─── Configuration ────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
FLOWS_DIR   = os.path.join(BASE_DIR, "flows")
DB_FILE     = RECORDED_ELEMENTS_FILE
MANUAL_FILE = MANUAL_LOCATORS_FILE
UNSCOPED_DATA_DIR = os.path.join(BASE_DIR, "data", "contexts", "_unscoped")
UNSCOPED_FLOWS_DIR = os.path.join(BASE_DIR, "flows", "contexts", "_unscoped")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

db_lock = threading.Lock()
runner_settings_lock = threading.Lock()

# ─── Shared server-side state ─────────────────────────────────────────────────
_ui_state: dict = {
    "flow_steps":    [],
    "known_pages":   [],
    "known_names":   [],
    "latest_names":  [],
    "last_element":  None,
    "edit_step_idx": None,
    "start_url":     "",
    "recording_context": None,
    "auth_session": None,
}


def _active_db_file() -> str:
    default_path = os.path.join(UNSCOPED_DATA_DIR, "recorded_elements.json")
    return rc.get_path(_ui_state.get("recording_context"), "recorded_elements_file", default_path)


def _active_manual_file() -> str:
    default_path = os.path.join(UNSCOPED_DATA_DIR, "locators_manual.json")
    return rc.get_path(_ui_state.get("recording_context"), "manual_locators_file", default_path)


def _active_flow_dir() -> str:
    return rc.get_path(_ui_state.get("recording_context"), "flow_dir", UNSCOPED_FLOWS_DIR)


def _has_context() -> bool:
    return bool(_ui_state.get("auth_session")) and bool(_ui_state.get("recording_context"))


def _has_auth_session() -> bool:
    return bool(_ui_state.get("auth_session"))


# =====================================================
# 2. LOCATOR ENGINE (DEDUPLICATED)
# =====================================================

def sanitize_and_match_identifier(raw_name: str, existing_keys: list) -> str:
    clean_name = re.sub(r'[\s\-]+', '_', raw_name.strip().lower())
    clean_name = re.sub(r'[^a-z0-9_]', '', clean_name)
    base_pattern = clean_name.replace('_', '')
    for existing_key in existing_keys:
        if base_pattern == existing_key.replace('_', ''):
            return existing_key
    return clean_name


def generate_safe_xpath(element_dna):
    tag = element_dna.get("tagName", "*")
    attrs = element_dna.get("attributes", {})

    if attrs.get("id"):
        return f"//{tag}[@id='{attrs['id']}']"
    if attrs.get("name"):
        return f"//{tag}[@name='{attrs['name']}']"
    if attrs.get("href"):
        return f"//{tag}[@href='{attrs['href']}']"
    if attrs.get("aria-label"):
        return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"):
        return f"//{tag}[@title='{attrs['title']}']"
    if attrs.get("alt"):
        return f"//{tag}[@alt='{attrs['alt']}']"

    text = element_dna.get("innerText") or ""
    text = text.strip() if text else ""
    _text_safe = text and len(text) < 50 and "'" not in text and '"' not in text

    classes = attrs.get("class", "")
    if classes:
        valid_classes = [c for c in classes.split() if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join([f"contains(@class,'{c}')" for c in valid_classes])
            # Append innerText anchor so sibling elements with same class are unique
            if _text_safe:
                return f"//{tag}[{contains_logic} and normalize-space(.)='{text}']"
            return f"//{tag}[{contains_logic}]"

    if text and len(text) < 50:
        if "'" in text and '"' not in text:
            return f'//{tag}[normalize-space(text())="{text}"]'
        elif '"' in text and "'" not in text:
            return f"//{tag}[normalize-space(text())='{text}']"
        elif "'" in text and '"' in text:
            return f"//{tag}"
        else:
            return f"//{tag}[normalize-space(text())='{text}']"

    return f"//{tag}"


# =====================================================
# 3. DISK I/O MANAGER
# =====================================================

def read_database_unlocked():
    db_file = _active_db_file()
    if not os.path.exists(db_file):
        return {}
    try:
        with open(db_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        logging.error("Database corrupted → quarantining")
        backup_path = db_file + ".corrupt.bak"
        try:
            shutil.copy2(db_file, backup_path)
            logging.info(f"Backup created → {backup_path}")
        except Exception:
            logging.exception("Backup failed")
        return {}
    except Exception:
        # ARCHITECTURAL FIX: Catch-all prevents OS-level file lock crashes
        logging.exception("Database read failure")
        return {}


def write_database_unlocked(data):
    db_file = _active_db_file()
    os.makedirs(os.path.dirname(db_file), exist_ok=True)
    with open(db_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())


# =====================================================
# 4. LOCATOR PERSISTENCE ENGINE
# =====================================================

def persist_element_to_disk(element_dna):
    with db_lock:
        data = read_database_unlocked()

        raw_page = element_dna.pop("userPageName", "global_context")
        raw_locator = element_dna.pop("userLocatorName", "unnamed_locator")

        safe_page_name = sanitize_and_match_identifier(raw_page, list(data.keys()))

        if safe_page_name not in data:
            data[safe_page_name] = {}

        existing_locators = list(data[safe_page_name].keys())
        clean_locator = re.sub(r'[\s\-]+', '_', raw_locator.strip().lower())
        clean_locator = re.sub(r'[^a-z0-9_]', '', clean_locator)

        safe_locator_name = clean_locator

        element_dna["custom_xpath"] = generate_safe_xpath(element_dna)

        if safe_locator_name in data[safe_page_name]:
            data[safe_page_name][safe_locator_name].update(element_dna)
            logging.info(f"UPDATED locator → {safe_page_name}.{safe_locator_name}")
        else:
            data[safe_page_name][safe_locator_name] = element_dna
            logging.info(f"SAVED locator → {safe_page_name}.{safe_locator_name}")

        write_database_unlocked(data)

        # Update shared UI state lists
        if safe_page_name not in _ui_state["known_pages"]:
            _ui_state["known_pages"].append(safe_page_name)
        if safe_locator_name not in _ui_state["known_names"]:
            _ui_state["known_names"].append(safe_locator_name)
        lst = _ui_state["latest_names"]
        if safe_locator_name in lst:
            lst.remove(safe_locator_name)
        lst.insert(0, safe_locator_name)
        _ui_state["latest_names"] = lst[:3]

        return safe_locator_name, data[safe_page_name][safe_locator_name]


# ─── Manual locators helpers ──────────────────────────────────────────────────────
def _load_manual_locators() -> dict:
    manual_file = _active_manual_file()
    if not os.path.exists(manual_file):
        return {}
    try:
        with open(manual_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _persist_manual_locator(page_name: str, element_name: str, locator_data: dict):
    """Store a manually-built element into data/locators_manual.json under web > page_name."""
    key = page_name.strip() or "global_context"
    manual_file = _active_manual_file()
    os.makedirs(os.path.dirname(manual_file), exist_ok=True)
    lock_path = manual_file + ".lock"
    with open(lock_path, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX)
            data = _load_manual_locators()
            data.setdefault("web", {}).setdefault(key, {})[element_name] = locator_data
            with open(manual_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
    if key not in _ui_state["known_pages"]:
        _ui_state["known_pages"].append(key)
    if element_name not in _ui_state["known_names"]:
        _ui_state["known_names"].append(element_name)
    lst = _ui_state["latest_names"]
    if element_name in lst:
        lst.remove(element_name)
    lst.insert(0, element_name)
    _ui_state["latest_names"] = lst[:3]
    # Signal the UI tick to push the new name into the dropdown immediately
    _ui_state.setdefault("pending_new_names", []).append(element_name)


def _seed_known_from_db() -> bool:
    """Merge known_pages + known_names from both database files.

    Returns True when any new page/name is discovered.
    """
    changed = False
    try:
        data = read_database_unlocked()
        for page, elements in data.items():
            if page not in _ui_state["known_pages"]:
                _ui_state["known_pages"].append(page)
                changed = True
            if isinstance(elements, dict):
                for n in elements:
                    if n not in _ui_state["known_names"]:
                        _ui_state["known_names"].append(n)
                        changed = True
    except Exception:
        pass
    try:
        manual = _load_manual_locators()
        web_data = manual.get("web", manual)
        for page, elements in web_data.items():
            if isinstance(elements, dict):
                if page not in _ui_state["known_pages"]:
                    _ui_state["known_pages"].append(page)
                    changed = True
                for n in elements:
                    if n not in _ui_state["known_names"]:
                        _ui_state["known_names"].append(n)
                        changed = True
    except Exception:
        pass
    return changed


# ─── Flow helpers ──────────────────────────────────────────────────────────────
def _save_flow(name: str, steps: list) -> str:
    flow_dir = _active_flow_dir()
    os.makedirs(flow_dir, exist_ok=True)
    safe = re.sub(r"[^a-z0-9_]", "_", name.lower().strip("_"))
    safe = re.sub(r"_+", "_", safe).strip("_") or "recorded_flow"
    path = os.path.join(flow_dir, f"{safe}.flow")
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M")
    ctx = _ui_state.get("recording_context") or {}
    ctx_txt = ""
    if ctx:
        p = ctx.get("platform", {}).get("label", "")
        prj = ctx.get("project_name", "")
        scr = ctx.get("script_name", "")
        ctx_txt = f"# Context {p} / {prj} / {scr}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {os.path.relpath(path, BASE_DIR)}\n")
        if ctx_txt:
            f.write(ctx_txt)
        f.write(f"# Recorded {ts} \u2014 platform: WEB\n")
        f.write("# \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n")
        for s in steps:
            f.write(s + "\n")
    return path


def _build_web_step(name: str, action: str, extra: str,
                   if_visible: bool = False, wait_secs: float = 0,
                   el_index: str = "any", scroll_first: bool = False) -> str | None:
    """Build a .flow step string for web actions."""
    wait_sfx = (
        f" wait {int(wait_secs) if wait_secs == int(wait_secs) else wait_secs} seconds"
        if (if_visible and wait_secs > 0) else ""
    )
    vis     = " if visible" if if_visible else ""
    idx_sfx = "" if (not el_index or el_index == "any") else f"[{el_index}]"

    def _n(base): return f"{base}{idx_sfx}" if idx_sfx else base

    scroll_pfx = f"scroll to element {name}\n" if scroll_first and name else ""

    if action == "click":
        return f"{scroll_pfx}click{vis} {_n(name)}{wait_sfx}"
    elif action == "type":
        return f'{scroll_pfx}type "{extra or "input_text"}" into {_n(name)}'
    elif action == "verify exists":
        return f"{scroll_pfx}verify element exists {_n(name)}"
    elif action == "verify text":
        return f'verify text "{extra or name}" in {_n(name)}'
    elif action == "double click":
        return f"{scroll_pfx}double click{vis} {_n(name)}{wait_sfx}"
    elif action == "hover":
        return f"{scroll_pfx}hover over {_n(name)}"
    elif action == "wait for element":
        return f"wait for element {_n(name)}"
    elif action == "store text":
        return f"{scroll_pfx}store text from {_n(name)} as {extra or name + '_text'}"
    elif action == "scroll to element":
        return f"scroll to element {name}"
    elif action == "screenshot":
        return f"take screenshot as {extra or name}"
    elif action == "wait":
        return f"wait {extra or '1'} seconds"
    elif action == "go to url":
        return f"go to url {extra or name}"
    elif action == "press key":
        return f"press key {extra or 'Enter'}"
    elif action == "scroll down":
        return "scroll down"
    elif action == "scroll up":
        return "scroll up"
    elif action == "press enter":
        return "press enter"
    elif action == "go back":
        return "go back"
    elif action == "go forward":
        return "go forward"
    elif action == "refresh":
        return "refresh page"
    # ── Fake data ────────────────────────────────────────────────────────────
    elif action in ("fake name", "fake email", "fake phone", "fake uuid",
                    "fake number", "fake address", "fake company",
                    "fake username", "fake password"):
        kind = action.replace("fake ", "")
        var  = extra or f"{kind.replace(' ', '_')}_val"
        return f"generate fake {kind} as {var}"
    elif action == "random number":
        parts = (extra or "1 1000").split()
        mn, mx = (parts + ["1000"])[0], (parts + ["1000", "1000"])[1]
        var = name or "rand_num"
        return f"generate random number {mn} {mx} as {var}"
    elif action == "random string":
        length = extra or "8"
        var = name or "rand_str"
        return f"generate random string {length} as {var}"
    # ── Date / Time ──────────────────────────────────────────────────────────
    elif action == "get today":
        return f"get today as {extra or 'today_date'}"
    elif action == "get timestamp":
        return f"get timestamp as {extra or 'ts'}"
    elif action == "get date offset":
        offset = extra or "+1"
        var = name or "offset_date"
        return f"get date {offset} days as {var}"
    # ── HTTP / API ───────────────────────────────────────────────────────────
    elif action == "api get":
        url = extra or "https://api.example.com/endpoint"
        var = name or "api_response"
        return f'api get "{url}" as {var}'
    elif action == "api post":
        url  = extra or "https://api.example.com/endpoint"
        body = '{"key": "value"}'
        var  = name or "api_response"
        return f"api post \"{url}\" with body '{body}' as {var}"
    elif action == "store json path":
        src  = extra or "api_response"
        path = name or "data.0.id"
        var  = extra or "extracted_val"
        return f"store json {src} path {path} as {var}"
    # ── Excel / CSV ──────────────────────────────────────────────────────────
    elif action == "read excel cell":
        file = extra or "data/test_data.xlsx"
        row  = "1"
        col  = "1"
        var  = name or "cell_val"
        return f'read excel "{file}" row {row} col {col} as {var}'
    elif action == "read excel row":
        file = extra or "data/test_data.xlsx"
        row  = "1"
        var  = name or "row_data"
        return f'read excel "{file}" row {row} as {var}'
    elif action == "read csv cell":
        file = extra or "data/test_data.csv"
        row  = "1"
        col  = "1"
        var  = name or "csv_val"
        return f'read csv "{file}" row {row} col {col} as {var}'
    # ── JavaScript Actions ───────────────────────────────────────────────────
    elif action == "js click":
        return f"js click {name}"
    elif action == "js scroll to":
        return f"js scroll to {name}"
    elif action == "js scroll down":
        return f"js scroll down {extra or '300'}"
    elif action == "js scroll up":
        return f"js scroll up {extra or '300'}"
    elif action == "js scroll top":
        return "js scroll top"
    elif action == "js scroll bottom":
        return "js scroll bottom"
    elif action == "js type":
        return f'js type "{extra or "text"}" into {name}'
    elif action == "js focus":
        return f"js focus {name}"
    elif action == "js submit":
        return f"js submit {name}"
    elif action == "js dispatch":
        return f"js dispatch {extra or 'change'} on {name}"
    return None


# ─── API ROUTES & LIFECYCLE ─────────────────────────────────────────────────────

middleware_already_added = any(m.cls == CORSMiddleware for m in app.user_middleware)
if not middleware_already_added:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"]
    )

def trigger_graceful_shutdown():
    logging.info("Shutting down codeless engine cleanly...")
    logging.info("Port 8080 released.")

app.on_shutdown(trigger_graceful_shutdown)    

@app.get("/api/get-database-schema")
def get_database_schema():
    with db_lock:
        return read_database_unlocked()

@app.post("/api/record-element")
async def receive_recorded_element(request: Request):
    if not _has_context():
        return {
            "status": "error",
            "message": "Login + recording context are required before recording elements.",
        }
    try:
        raw_dna = await request.json()
    except Exception:
        logger.exception("Invalid JSON payload")
        return {"status": "error", "message": "Invalid JSON"}

    try:
        page_hint    = raw_dna.get("userPageName", "global_context")
        locator_hint = raw_dna.get("userLocatorName", "unnamed_locator")

        locator_name, processed_dna = persist_element_to_disk(raw_dna)

        _ui_state["last_element"] = {
            "page":  page_hint,
            "name":  locator_name,
            "hint":  locator_hint,
            "xpath": processed_dna.get("custom_xpath", ""),
            "tag":   processed_dna.get("tagName", ""),
            "text":  (processed_dna.get("innerText") or "")[:60],
        }
        rs.log_action(
            _ui_state.get("auth_session"),
            "record_element_extension",
            {"page": page_hint, "locator_name": locator_name},
        )

        logger.info("Recorded element → %s.%s", page_hint, locator_name)
        return {"status": "success", "locator_name": locator_name}
    except Exception:
        logger.exception("Recording failure")
        return {"status": "error"}


@app.post("/api/set-start-url")
async def set_start_url(request: Request):
    try:
        body = await request.json()
        url = body.get("url", "").strip()
        if url:
            _ui_state["start_url"] = url
            logger.info("Start URL set → %s", url)
        return {"status": "ok"}
    except Exception:
        return {"status": "error"}


# ─── NiceGUI PAGE ─────────────────────────────────────────────────────────────

def build_web_ui():
    """Web recorder panel — full-screen DevTools-style, side panel docked in Chrome."""
    _seed_known_from_db()
    _locator_store_mtime = {
        "recorded_file": _active_db_file(),
        "manual_file": _active_manual_file(),
        "recorded": rc.file_mtime(_active_db_file()),
        "manual": rc.file_mtime(_active_manual_file()),
    }

    def _locator_store_changed() -> bool:
        recorded_file = _active_db_file()
        manual_file = _active_manual_file()
        recorded_mtime = rc.file_mtime(recorded_file)
        manual_mtime = rc.file_mtime(manual_file)
        path_changed = (
            recorded_file != _locator_store_mtime["recorded_file"]
            or manual_file != _locator_store_mtime["manual_file"]
        )
        changed = (
            path_changed
            or
            recorded_mtime != _locator_store_mtime["recorded"]
            or manual_mtime != _locator_store_mtime["manual"]
        )
        if changed:
            _locator_store_mtime["recorded_file"] = recorded_file
            _locator_store_mtime["manual_file"] = manual_file
            _locator_store_mtime["recorded"] = recorded_mtime
            _locator_store_mtime["manual"] = manual_mtime
        return changed

    # Enable Quasar dark mode so all components render with light text on dark backgrounds
    ui.dark_mode().enable()

    ui.page_title("🌐 Web Recorder")

    ui.add_head_html("""
    <style>
      * { box-sizing:border-box; }
      body { background:#0f172a !important; color:#e2e8f0;
             font-family:'Inter',sans-serif; margin:0; padding:0; }
      .q-card { background:#1e293b !important; border:1px solid #334155; }
      ::-webkit-scrollbar { width:6px; height:6px; }
      ::-webkit-scrollbar-thumb { background:#475569; border-radius:3px; }
      .step-row { border-bottom:1px solid #334155; padding:3px 6px; }
      .step-row:hover { background:#1e293b; cursor:pointer; }

      /* Main container — fixed to viewport, independent of Quasar's layout chain */
      .web-recorder-root {
        position:fixed; inset:0; z-index:10;
        display:flex; flex-direction:column;
        overflow:hidden;
        background:#0f172a;
      }
      /* Tip banner at the top */
      .chrome-tip-banner {
        display:flex; align-items:center; gap:10px;
        padding:6px 14px;
        background:#1e293b; border-bottom:2px solid #3b82f6;
        flex-shrink:0; min-height:36px;
      }
      .chrome-tip-banner .tip-text {
        font-size:11px; color:#94a3b8; flex:1; white-space:nowrap;
        overflow:hidden; text-overflow:ellipsis;
      }
      .chrome-tip-banner .tip-status {
        font-size:11px; color:#22c55e; flex-shrink:0;
      }

      /* DevTools panel — fills everything below the banner */
      #devtools-panel {
        background:#1a1f2e; border-top:none;
        display:flex; flex-direction:column; overflow:hidden;
        flex:1; min-height:0;
      }
      .panel-toolbar {
        display:flex; align-items:center; gap:12px;
        padding:4px 12px; background:#0f172a;
        border-bottom:1px solid #334155; flex-shrink:0; min-height:34px;
      }
      .panel-body {
        flex:1; overflow-y:auto; overflow-x:hidden;
        padding:10px 14px; display:flex; flex-direction:column; gap:10px;
      }
    </style>
    """)

    with ui.element("div").classes("web-recorder-root"):

        # ── TIP BANNER ────────────────────────────────────────────────────────
        with ui.element("div").classes("chrome-tip-banner"):
            ui.html('<span style="font-size:16px;flex-shrink:0;">🔬</span>')
            ui.html(
                '<span class="tip-text">'
                '  Browse in <b style="color:#60a5fa;">Chrome</b> — '
                '  <b style="color:#fbbf24;">Alt+Click</b> (Win) / '
                '  <b style="color:#fbbf24;">Option+Click</b> (Mac) any element to record it'
                '</span>'
            )
            panel_status_lbl = ui.label("Ready").classes("tip-status")

        # ── DEVTOOLS PANEL (fills remaining height) ───────────────────────────
        with ui.element("div").props('id="devtools-panel"'):

            with ui.element("div").classes("panel-toolbar"):
                ui.html('<span style="color:#60a5fa;font-weight:700;font-size:12px;">✏️ Record Element</span>')

            with ui.element("div").classes("panel-body"):
                active_auth = _ui_state.get("auth_session") or {}
                with ui.card().classes("w-full p-3 border border-emerald-700 bg-slate-900"):
                    ui.label("🛂 Employee Login (Mandatory)").classes("text-emerald-300 font-bold text-sm")
                    with ui.row().classes("w-full gap-2 items-end flex-wrap mt-1"):
                        auth_name_input = ui.input(
                            label="Employee name",
                            value=active_auth.get("employee_name", ""),
                        ).classes("w-52").props("dense clearable")
                        auth_id_input = ui.input(
                            label="Employee ID",
                            value=active_auth.get("employee_id", ""),
                        ).classes("w-32").props("dense clearable")
                        auth_login_btn = ui.button("🔐 Login", color="green").classes("text-xs")
                        auth_logout_btn = ui.button("🚪 Logout", color="red").classes("text-xs")
                    auth_status_lbl = ui.label(
                        "Login required before context activation and recording."
                    ).classes("text-xs text-slate-300")

                active_ctx = _ui_state.get("recording_context") or {}
                active_platform = (
                    active_ctx.get("platform", {}).get("label")
                    if isinstance(active_ctx.get("platform"), dict) else None
                ) or "Website"
                active_project = active_ctx.get("project_name", "")
                active_script = active_ctx.get("script_name", "")

                # Strict preflight context: required before any recording persistence.
                with ui.card().classes("w-full p-3 border border-amber-700 bg-slate-900"):
                    ui.label("🧭 Recording Context (Mandatory)").classes("text-amber-300 font-bold text-sm")
                    with ui.row().classes("w-full gap-2 items-end flex-wrap mt-1"):
                        context_platform_select = ui.select(
                            rc.list_platform_labels(),
                            value=active_platform,
                            label="Platform",
                        ).classes("w-40").props("dense")
                        context_project_input = ui.input(
                            label="Project name", value=active_project or ""
                        ).classes("w-40").props("dense clearable")
                        context_script_input = ui.input(
                            label="Test script", value=active_script or ""
                        ).classes("w-40").props("dense clearable")
                        activate_context_btn = ui.button("✅ Activate Context", color="orange").classes("text-xs")
                    context_status_lbl = ui.label(
                        "Select platform + project + test script to unlock recording."
                    ).classes("text-xs text-slate-300")

                # ── STEP 1 — Navigate to URL (always the first step) ──────────────
                with ui.element("div").style(
                    "background:linear-gradient(135deg,#1e3a5f 0%,#1e293b 100%);"
                    "border:2px solid #3b82f6;border-radius:8px;"
                    "padding:10px 12px;display:flex;flex-direction:column;gap:6px;"
                ):
                    with ui.element("div").style("display:flex;gap:6px;align-items:center;"):
                        ui.html('<span style="font-size:13px;">🌐</span>')
                        ui.html(
                            '<span style="color:#60a5fa;font-weight:700;font-size:11px;'
                            'text-transform:uppercase;letter-spacing:.06em;">'
                            'Step 1 — Navigate to URL</span>'
                        )
                        ui.html(
                            '<span style="color:#94a3b8;font-size:10px;margin-left:4px;">'
                            '(auto-fills from side-panel · editable)</span>'
                        )
                    with ui.element("div").style("display:flex;gap:6px;align-items:center;"):
                        start_url_input = ui.input(
                            placeholder="https://example.com"
                        ).style(
                            "flex:1;background:#0f172a;border:1px solid #3b82f6;"
                            "border-radius:4px;color:#e2e8f0;font-size:12px;padding:4px 8px;"
                        ).props("dense clearable")
                        ui.html(
                            '<span style="color:#64748b;font-size:10px;white-space:nowrap;">'
                            'auto-prepended on Save</span>'
                        )

                # Last recorded indicator
                with ui.element("div").style(
                    "background:#1e293b;border:1px solid #334155;border-radius:6px;"
                    "padding:6px 10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;"
                ):
                    ui.html('<span style="color:#94a3b8;font-size:11px;">📍 Last recorded:</span>')
                    last_el_detail = ui.label(
                        "None yet — Alt+Click (Win) / Option+Click (Mac) on any element"
                    ).classes("text-xs text-cyan-300 font-mono flex-1 truncate")

                # Record Element form
                with ui.card().classes("w-full p-3"):
                    with ui.row().classes("justify-between items-center mb-2"):
                        ui.label("✏️ Record Element").classes("text-blue-400 font-bold text-sm")
                        if_visible_toggle = ui.switch("If Visible").classes("text-xs text-yellow-300")

                    # Row A: Page name + Element name + Action + Index + Add button
                    with ui.row().classes("gap-2 items-end flex-wrap w-full"):
                        page_name_input = ui.select(
                            options=list(_ui_state["known_pages"]),
                            value=None, label="Page name",
                            new_value_mode="add-unique", with_input=True,
                        ).classes("w-36 text-sm").props("dense clearable")

                        name_input = ui.select(
                            options=list(_ui_state["known_names"]),
                            value=None, label="Element name",
                            new_value_mode="add-unique", with_input=True,
                        ).classes("flex-1 min-w-36 text-sm").props("dense clearable")

                        action_select = ui.select(
                            # ── Element actions ─────────────────────────────
                            ["click", "type", "verify exists", "verify text",
                             "double click", "hover", "wait for element",
                             "store text", "scroll to element",
                             # ── Page actions ────────────────────────────────
                             "scroll down", "scroll up", "screenshot",
                             "wait", "go to url", "go back", "go forward",
                             "refresh", "press key", "press enter",
                             # ── Test data (Faker) ────────────────────────────
                             "fake name", "fake email", "fake phone",
                             "fake uuid", "fake number", "fake address",
                             "fake company", "fake username", "fake password",
                             "random number", "random string",
                             # ── Date / Time ──────────────────────────────────
                             "get today", "get timestamp", "get date offset",
                             # ── HTTP / API ───────────────────────────────────
                             "api get", "api post", "store json path",
                             # ── Excel / CSV ──────────────────────────────────
                             "read excel cell", "read excel row", "read csv cell",
                             # ── JavaScript (bypass overlays / React) ─────────────
                             "js click", "js scroll to",
                             "js scroll down", "js scroll up",
                             "js scroll top", "js scroll bottom",
                             "js type", "js focus", "js submit", "js dispatch"],
                            value="click", label="Action",
                        ).classes("w-40").props("dense").props("use-input input-debounce=0 hide-selected fill-input")

                        index_select = ui.select(
                            ["any", "1st", "2nd", "3rd", "4th", "5th",
                             "last", "last-2", "last-3", "last-4"],
                            value="any", label="Index",
                        ).classes("w-24").props("dense")

                        record_btn = ui.button("⚡ Add Step", color="blue").classes("text-sm font-bold")

                    # Row B: Extra/var + scroll toggle + wait secs
                    with ui.row().classes("gap-2 items-end flex-wrap w-full mt-1"):
                        try:
                            from nlp.variable_manager import RUNTIME_VARIABLES
                            _var_opts = [f"${{{k}}}" for k in RUNTIME_VARIABLES]
                        except Exception:
                            _var_opts = []

                        extra_input = ui.select(
                            options=_var_opts,
                            value=None, label="Extra (text / ${var} / URL / seconds)",
                            new_value_mode="add-unique", with_input=True,
                        ).classes("flex-1 text-sm").props("dense clearable")

                        scroll_toggle = ui.switch("Scroll to").classes("text-xs text-cyan-300")

                    # Contextual hint — updates when action changes
                    _ACTION_HINTS = {
                        # Element
                        "click":           "Element name → click it",
                        "type":            "Extra = text to type  |  Element name = target field",
                        "verify exists":   "Element name → assert it exists on page",
                        "verify text":     "Extra = expected text  |  Element name = element",
                        "store text":      "Element name = source  |  Extra = variable name to save into",
                        "hover":           "Element name → hover over it (no click)",
                        "wait for element":"Element name → wait until it appears",
                        # Page
                        "go to url":       "Extra = full URL  e.g.  https://example.com",
                        "go back":         "No inputs needed — browser history back",
                        "go forward":      "No inputs needed — browser history forward",
                        "refresh":         "No inputs needed — reload current page",
                        "wait":            "Extra = seconds  e.g.  2",
                        "press key":       "Extra = key name  e.g.  Tab  Escape  ArrowDown",
                        "screenshot":      "Extra = label name for the screenshot file",
                        # Fake data
                        "fake name":       "Extra = variable name to store result  e.g.  user_name",
                        "fake email":      "Extra = variable name  e.g.  reg_email",
                        "fake phone":      "Extra = variable name  e.g.  phone_num",
                        "fake uuid":       "Extra = variable name  e.g.  request_id",
                        "fake number":     "Extra = variable name  e.g.  item_count",
                        "fake address":    "Extra = variable name  e.g.  user_address",
                        "fake company":    "Extra = variable name  e.g.  company_name",
                        "fake username":   "Extra = variable name  e.g.  login_user",
                        "fake password":   "Extra = variable name  e.g.  login_pass",
                        "random number":   "Extra = min max  e.g.  1000 9999  |  Element = variable name",
                        "random string":   "Extra = length  e.g.  8  |  Element = variable name",
                        # Date / Time
                        "get today":       "Extra = variable name  e.g.  today_date  → stores 03/03/2026",
                        "get timestamp":   "Extra = variable name  e.g.  ts  → stores 2026-03-03 05:50:08",
                        "get date offset": "Extra = +N or -N  e.g.  +7  |  Element = variable name",
                        # HTTP / API
                        "api get":         "Extra = URL  |  Element = variable to store response  e.g.  api_response",
                        "api post":        "Extra = URL  |  Element = variable name  (body is a default template)",
                        "store json path": "Extra = source variable  |  Element = dot-path  e.g.  data.0.email",
                        # Excel / CSV
                        "read excel cell": "Extra = file path  e.g.  data/sheet.xlsx  |  Element = variable name",
                        "read excel row":  "Extra = file path  e.g.  data/sheet.xlsx  |  Element = variable name",
                        "read csv cell":   "Extra = file path  e.g.  data/users.csv  |  Element = variable name",
                        # JavaScript
                        "js click":        "Element = target  — JS el.click() bypasses overlays / pointer-events:none",
                        "js scroll to":    "Element = target  — scrollIntoView (smooth)",
                        "js scroll down":  "Extra = pixels  e.g.  300  — window.scrollBy(0, N)",
                        "js scroll up":    "Extra = pixels  e.g.  300  — window.scrollBy(0, -N)",
                        "js scroll top":   "No inputs needed  — scroll to very top",
                        "js scroll bottom":"No inputs needed  — scroll to very bottom",
                        "js type":         "Extra = text  |  Element = field  — React-aware value setter",
                        "js focus":        "Element = target  — el.focus() + focus events",
                        "js submit":       "Element = form  — el.submit() or dispatch submit event",
                        "js dispatch":     "Extra = event name  e.g.  change  |  Element = target",
                    }
                    hint_lbl = ui.label("").classes(
                        "text-xs text-amber-300 w-full px-1 py-0.5 rounded"
                    ).style("background:#1c1917;min-height:18px;font-style:italic;")

                    def _update_hint(e=None):
                        act = action_select.value or ""
                        hint_lbl.set_text(_ACTION_HINTS.get(act, ""))
                    action_select.on("update:model-value", _update_hint)

                    with ui.row().classes("gap-2 items-end flex-wrap w-full"):
                        wait_secs_input = ui.number(
                            label="If-Vis wait (s)", value=0, min=0, max=60, step=1
                        ).classes("w-28").props("dense")
                        wait_secs_input.set_visibility(False)

                    def on_if_visible_toggle(e):
                        wait_secs_input.set_visibility(e.value)
                    if_visible_toggle.on("update:model-value", on_if_visible_toggle)

                # Flow steps + Live state (side by side)
                with ui.row().classes("w-full gap-3 items-start flex-wrap"):

                    with ui.card().classes("flex-1 p-2 min-w-64"):
                        with ui.row().classes("justify-between items-center mb-1"):
                            ui.label("📋 Flow Steps").classes("text-blue-400 font-bold text-xs")
                            step_count_lbl = ui.label("0 steps").classes("text-xs text-slate-400")

                        flow_scroll = ui.scroll_area().style("height:180px;").classes("w-full")

                        with ui.row().classes("gap-1 flex-wrap mt-1"):
                            def qadd(s):
                                if not _has_context():
                                    ui.notify("Activate recording context first", color="negative"); return
                                _ui_state["flow_steps"].append(s)
                                _update_flow_list()
                                ui.notify(f"Added: {s}", color="positive")

                            ui.button("⬇ Scroll", on_click=lambda: qadd("scroll down")
                            ).props("flat dense").classes("text-xs bg-slate-700")
                            ui.button("⬆ Scroll", on_click=lambda: qadd("scroll up")
                            ).props("flat dense").classes("text-xs bg-slate-700")
                            ui.button("↵ Enter",  on_click=lambda: qadd("press enter")
                            ).props("flat dense").classes("text-xs bg-slate-700")

                            wait_q = ui.number(label="Wait(s)", value=1, min=0.5, step=0.5).classes("w-20")
                            ui.button("⏱", on_click=lambda: qadd(
                                f"wait {int(wait_q.value) if float(wait_q.value)==int(wait_q.value) else wait_q.value} seconds"
                            )).props("flat dense").classes("text-xs bg-slate-700")

                        with ui.row().classes("gap-1 mt-1"):
                            def do_undo():
                                if _ui_state["flow_steps"]:
                                    removed = _ui_state["flow_steps"].pop()
                                    _update_flow_list()
                                    ui.notify(f"Undone: {removed}", color="warning")
                                    rs.log_action(
                                        _ui_state.get("auth_session"),
                                        "delete_step",
                                        {"mode": "undo", "step": removed},
                                    )

                            def do_clear():
                                _ui_state["flow_steps"].clear()
                                _ui_state["edit_step_idx"] = None
                                _update_flow_list()
                                rs.log_action(
                                    _ui_state.get("auth_session"),
                                    "delete_step",
                                    {"mode": "clear_all"},
                                )

                            ui.button("↩ Undo",  on_click=do_undo ).props("flat dense").classes("text-xs bg-slate-700")
                            ui.button("🗑 Clear", on_click=do_clear).props("flat dense").classes("text-xs bg-red-900")

                    with ui.card().classes("w-52 p-2 bg-indigo-950 border border-indigo-700"):
                        ui.label("📡 Live State").classes("text-indigo-300 font-bold text-xs mb-1")
                        live_panel = ui.column().classes("gap-0 w-full")

                # Save + Load flow
                with ui.row().classes("w-full gap-2 items-end flex-wrap"):
                    flow_name_input = ui.input(
                        label="Flow name", value="web_recorded"
                    ).classes("w-44").props("dense")

                    def do_save():
                        if not _has_context():
                            ui.notify("Activate recording context first", color="negative"); return
                        name_ = flow_name_input.value.strip()
                        if not _ui_state["flow_steps"]:
                            ui.notify("No steps to save", color="negative"); return
                        if not name_:
                            ui.notify("Enter a flow name", color="negative"); return
                        steps_to_save = list(_ui_state["flow_steps"])
                        # Prepend go-to-url if set and not already the first step
                        url_ = start_url_input.value.strip()
                        if url_:
                            if not url_.startswith(("http://", "https://")):
                                url_ = "https://" + url_
                            first_step = f"go to url {url_}"
                            if not steps_to_save or not steps_to_save[0].startswith("go to url"):
                                steps_to_save.insert(0, first_step)
                        path_ = _save_flow(name_, steps_to_save)
                        ui.notify(f"✅ Saved: {path_}", color="positive")
                        _refresh_flow_select()
                        _snapshot_context_state(
                            setup_options={
                                "last_saved_flow": os.path.basename(path_),
                                "step_count": len(steps_to_save),
                            }
                        )
                        rs.log_action(
                            _ui_state.get("auth_session"),
                            "save_flow",
                            {"flow_file": os.path.basename(path_), "step_count": len(steps_to_save)},
                        )

                    ui.button("💾 Save .flow", on_click=do_save, color="green"
                    ).props("flat dense").classes("text-sm")

                    flow_dir = _active_flow_dir()
                    os.makedirs(flow_dir, exist_ok=True)
                    _flist = sorted([f for f in os.listdir(flow_dir) if f.endswith(".flow")])
                    open_select = ui.select(
                        _flist or ["(none)"],
                        value=_flist[0] if _flist else "(none)",
                        label="Load .flow",
                    ).classes("w-44").props("dense")

                    def _refresh_flow_select():
                        active_dir = _active_flow_dir()
                        os.makedirs(active_dir, exist_ok=True)
                        fl = sorted([f for f in os.listdir(active_dir) if f.endswith(".flow")])
                        open_select.options = fl or ["(none)"]
                        if fl:
                            open_select.set_value(fl[0])
                        else:
                            open_select.set_value("(none)")
                        open_select.update()

                    def do_load():
                        if not _has_context():
                            ui.notify("Activate recording context first", color="negative"); return
                        fname = open_select.value
                        if not fname or fname == "(none)": return
                        path_ = os.path.join(_active_flow_dir(), fname)
                        if not os.path.exists(path_): return
                        steps_ = []
                        with open(path_, "r", encoding="utf-8") as fh:
                            for line in fh:
                                s = line.strip()
                                if s and not s.startswith("#"):
                                    steps_.append(s)
                        _ui_state["flow_steps"] = steps_
                        _update_flow_list()
                        ui.notify(f"Loaded {len(steps_)} steps from {fname}", color="positive")
                        rs.log_action(
                            _ui_state.get("auth_session"),
                            "load_flow",
                            {"flow_file": fname, "step_count": len(steps_)},
                        )

                    ui.button("📂 Load", on_click=do_load, color="teal"
                    ).props("flat dense").classes("text-sm")

                # ── Run Flow ─────────────────────────────────────────────────
                with ui.card().classes("w-full p-2 mt-1 border border-green-800"):
                    with ui.row().classes("justify-between items-center mb-1"):
                        ui.label("▶ Run Flow").classes("text-green-400 font-bold text-xs")
                        web_run_status = ui.label("").classes("text-xs text-slate-400")
                    web_run_log = ui.log(max_lines=120).classes("w-full text-xs font-mono").style(
                        "height:150px; background:#0f172a; color:#86efac; border:1px solid #166534;"
                    )
                    with ui.row().classes("gap-1 mt-1 flex-wrap items-center"):
                        web_run_btn  = ui.button("▶ Run Flow", color="green").props("flat dense").classes("text-sm font-bold")
                        web_stop_btn = ui.button("⏹ Stop", color="red").props("flat dense").classes("text-sm")
                        web_stop_btn.set_visibility(False)
                    _web_run_state = {"cancelled": False}

                    async def do_run_web_flow():
                        if not _has_context():
                            ui.notify("Activate recording context first", color="negative"); return
                        steps = list(_ui_state["flow_steps"])
                        if not steps:
                            ui.notify("No steps to run — add steps first", color="negative"); return
                        run_dir = _active_flow_dir()
                        os.makedirs(run_dir, exist_ok=True)
                        tmp_path = os.path.join(run_dir, "__tmp_run__.flow")
                        url_ = (start_url_input.value or "").strip()
                        all_steps = list(steps)
                        if url_:
                            if not url_.startswith(("http://", "https://")):
                                url_ = "https://" + url_
                            if not all_steps or not all_steps[0].startswith("go to url"):
                                all_steps.insert(0, f"go to url {url_}")
                        with open(tmp_path, "w", encoding="utf-8") as _f:
                            _f.write("\n".join(all_steps) + "\n")
                        web_run_btn.set_visibility(False)
                        web_stop_btn.set_visibility(True)
                        web_run_log.clear()
                        _web_run_state["cancelled"] = False
                        web_run_status.set_text(f"▶ Running {len(all_steps)} steps\u2026")
                        import runner as _runner
                        from config import settings as _settings
                        active_manual = _active_manual_file()
                        active_recorded = _active_db_file()

                        def _do_run():
                            with runner_settings_lock:
                                prev_manual = _settings.MANUAL_LOCATORS_FILE
                                prev_recorded = _settings.RECORDED_ELEMENTS_FILE
                                _settings.MANUAL_LOCATORS_FILE = active_manual
                                _settings.RECORDED_ELEMENTS_FILE = active_recorded
                                try:
                                    return _runner.run_nlp_flow_collect(tmp_path)
                                finally:
                                    _settings.MANUAL_LOCATORS_FILE = prev_manual
                                    _settings.RECORDED_ELEMENTS_FILE = prev_recorded
                        try:
                            stats = await run.io_bound(_do_run)
                            for entry in stats["log"]:
                                web_run_log.push(entry)
                            web_run_status.set_text(
                                f"\u2705 {stats['passed']} passed  \u274c {stats['failed']} failed"
                            )
                            rs.log_action(
                                _ui_state.get("auth_session"),
                                "run_flow",
                                {
                                    "result": "completed",
                                    "passed": stats["passed"],
                                    "failed": stats["failed"],
                                    "total_steps": len(all_steps),
                                },
                            )
                        except Exception as _ex:
                            web_run_log.push(f"\u274c {_ex}")
                            web_run_status.set_text("\u274c Error")
                            rs.log_action(
                                _ui_state.get("auth_session"),
                                "run_flow",
                                {"result": "error", "error": str(_ex), "total_steps": len(all_steps)},
                            )
                        finally:
                            web_run_btn.set_visibility(True)
                            web_stop_btn.set_visibility(False)
                            try: os.unlink(tmp_path)
                            except Exception: pass

                    web_run_btn.on("click", do_run_web_flow)
                    def _web_do_stop():
                        _web_run_state["cancelled"] = True
                        web_run_status.set_text("\u23f9 Stop requested\u2026")
                    web_stop_btn.on("click", _web_do_stop)

                # ── Env Setup ────────────────────────────────────────────────
                with ui.card().classes("w-full p-3 mt-1 border border-yellow-700"):
                    with ui.row().classes("justify-between items-center mb-2"):
                        ui.label("\u2699\ufe0f Environment Setup").classes("text-yellow-400 font-bold text-xs")
                        env_status_lbl = ui.label("").classes("text-xs text-slate-400")
                    from config import environment_manager as em
                    _env_names  = em.list_envs()
                    _active_env = em.get_active_env_name()
                    with ui.row().classes("gap-2 items-end flex-wrap w-full"):
                        env_select = ui.select(
                            _env_names or ["local"], value=_active_env, label="Active Env",
                        ).classes("w-36").props("dense")
                        new_env_input = ui.input(label="New env name").classes("w-32").props("dense clearable")
                        def do_add_env():
                            n_ = (new_env_input.value or "").strip()
                            if not n_: return
                            em.save_env(n_, {})
                            opts_ = em.list_envs()
                            env_select.options = opts_; env_select.set_value(n_); env_select.update()
                            new_env_input.set_value("")
                            ui.notify(f"Created: {n_}", color="positive")
                            _reload_env_fields()
                        ui.button("+ New", on_click=do_add_env).props("flat dense").classes("text-xs bg-blue-900")
                        def do_delete_env():
                            n_ = env_select.value
                            if n_ == "local": ui.notify("Cannot delete 'local'", color="negative"); return
                            em.delete_env(n_)
                            opts_ = em.list_envs()
                            env_select.options = opts_ or ["local"]; env_select.set_value("local"); env_select.update()
                            ui.notify(f"Deleted: {n_}", color="warning")
                            _reload_env_fields()
                        ui.button("\U0001f5d1", on_click=do_delete_env, color="red").props("flat dense").classes("text-xs")
                    with ui.row().classes("gap-2 items-end flex-wrap w-full mt-1"):
                        env_src_input = ui.input(label="Domain Source", value="").classes("flex-1 min-w-36").props("dense clearable")
                        env_dst_input = ui.input(label="Domain Override", value="").classes("flex-1 min-w-36").props("dense clearable")
                        env_src_input.tooltip("e.g. www.justdial.com \u2014 URLs containing this will be rewritten")
                        env_dst_input.tooltip("e.g. staging2.justdial.com \u2014 replacement host")
                    with ui.row().classes("gap-2 items-end flex-wrap w-full mt-1"):
                        auth_type_select = ui.select(["none", "basic", "popup"], value="none", label="Auth").classes("w-24").props("dense")
                        env_user_input   = ui.input(label="Username", value="").classes("w-28").props("dense clearable")
                        env_pass_input   = ui.input(label="Password", value="").classes("w-28").props("dense clearable password")
                        env_user_input.set_visibility(False)
                        env_pass_input.set_visibility(False)
                    def on_auth_type_change(e=None):
                        show = (auth_type_select.value or "none") != "none"
                        env_user_input.set_visibility(show)
                        env_pass_input.set_visibility(show)
                    auth_type_select.on("update:model-value", on_auth_type_change)
                    def _reload_env_fields():
                        n_  = env_select.value or "local"
                        e_  = em.get_env(n_)
                        env_src_input.set_value(e_.get("domain_source", ""))
                        env_dst_input.set_value(e_.get("domain_override", ""))
                        auth_type_select.set_value(e_.get("auth_type", "none"))
                        env_user_input.set_value(e_.get("username", ""))
                        env_pass_input.set_value(e_.get("password", ""))
                        on_auth_type_change()
                        act_ = em.get_active_env_name()
                        _sym = "\u2705 active" if n_ == act_ else "\u2b55 inactive"
                        env_status_lbl.set_text(f"{_sym} \u2014 {n_}")
                    env_select.on("update:model-value", lambda e: _reload_env_fields())
                    _reload_env_fields()
                    with ui.row().classes("gap-2 mt-2"):
                        def do_save_env():
                            n_ = env_select.value or "local"
                            em.save_env(n_, {
                                "domain_source":   env_src_input.value  or "",
                                "domain_override": env_dst_input.value  or "",
                                "auth_type":       auth_type_select.value or "none",
                                "username":        env_user_input.value or "",
                                "password":        env_pass_input.value or "",
                            })
                            _snapshot_context_state(
                                setup_options={
                                    "saved_env_name": n_,
                                    "domain_source": env_src_input.value or "",
                                    "domain_override": env_dst_input.value or "",
                                    "auth_type": auth_type_select.value or "none",
                                }
                            )
                            rs.log_action(_ui_state.get("auth_session"), "save_env", {"env_name": n_})
                            ui.notify(f"\U0001f4be Saved env: {n_}", color="positive")
                        def do_apply_env():
                            do_save_env()
                            n_ = env_select.value or "local"
                            em.set_active_env(n_)
                            _snapshot_context_state(setup_options={"active_env_name": n_})
                            rs.log_action(_ui_state.get("auth_session"), "apply_env", {"env_name": n_})
                            env_status_lbl.set_text(f"\u2705 active \u2014 {n_}")
                            ui.notify(f"\u2705 Active env \u2192 {n_}", color="positive")
                        ui.button("\U0001f4be Save", on_click=do_save_env, color="teal").props("flat dense").classes("text-xs")
                        ui.button("\u2705 Apply (set active)", on_click=do_apply_env, color="green").props("flat dense").classes("text-xs font-bold")
                        ui.label("scope: all URL steps in this flow").classes("text-xs text-slate-500 self-center ml-2")

    # ── Inner helpers ──────────────────────────────────────────────────────────

    def _sanitise(raw: str) -> str:
        return re.sub(r"[^a-z0-9_]", "_",
                      (raw or "").strip().lower().replace("-", "_"))[:40].strip("_")

    def _refresh_name_options():
        name_input.options = list(_ui_state["known_names"])
        name_input.update()

    def _refresh_page_options():
        page_name_input.options = list(_ui_state["known_pages"])
        page_name_input.update()

    def _refresh_var_options():
        try:
            from nlp.variable_manager import RUNTIME_VARIABLES
            extra_input.options = [f"${{{k}}}" for k in RUNTIME_VARIABLES]
            extra_input.update()
        except Exception:
            pass

    def _login_employee():
        name = (auth_name_input.value or "").strip()
        emp_id = (auth_id_input.value or "").strip()
        try:
            session = rs.start_session(
                employee_name=name,
                employee_id=emp_id,
                recorder_kind="web",
                client_id="",
            )
        except ValueError as ex:
            ui.notify(str(ex), color="negative")
            auth_status_lbl.set_text("❌ Login failed")
            return
        _ui_state["auth_session"] = session
        auth_name_input.set_value(session["employee_name"])
        auth_id_input.set_value(session["employee_id"])
        auth_status_lbl.set_text(
            f"✅ Logged in: {session['employee_name']} ({session['employee_id']})"
        )
        ui.notify("Login successful", color="positive")

    def _logout_employee():
        sess = _ui_state.get("auth_session")
        rs.end_session(sess, reason="user_logout")
        _ui_state["auth_session"] = None
        _ui_state["recording_context"] = None
        _ui_state["flow_steps"] = []
        _ui_state["edit_step_idx"] = None
        _ui_state["known_pages"] = []
        _ui_state["known_names"] = []
        _ui_state["latest_names"] = []
        _ui_state["last_element"] = None
        _refresh_name_options()
        _refresh_page_options()
        _refresh_flow_select()
        _update_flow_list()
        _update_live_panel()
        auth_status_lbl.set_text("Logged out")
        context_status_lbl.set_text("Context cleared — login required")
        panel_status_lbl.set_text("Ready")
        ui.notify("Logged out", color="warning")

    auth_login_btn.on("click", _login_employee)
    auth_logout_btn.on("click", _logout_employee)
    if _has_auth_session():
        s0 = _ui_state["auth_session"]
        auth_status_lbl.set_text(
            f"✅ Logged in: {s0['employee_name']} ({s0['employee_id']})"
        )

    def _snapshot_context_state(setup_options: dict | None = None):
        ctx = _ui_state.get("recording_context")
        if not ctx:
            return
        try:
            from config import environment_manager as _em
            from nlp.variable_manager import RUNTIME_VARIABLES
            rc.snapshot_context_state(
                ctx,
                env_name=_em.get_active_env_name(),
                env_payload=_em.get_active_env(),
                runtime_variables=dict(RUNTIME_VARIABLES),
                setup_options=setup_options or {},
            )
        except Exception:
            logger.exception("Failed to snapshot context state")

    def _activate_recording_context():
        if not _has_auth_session():
            ui.notify("Employee login required before context activation", color="negative")
            return
        plat = context_platform_select.value
        project = context_project_input.value or ""
        script = context_script_input.value or ""
        allowed_for_web = {"Website", "Touch-mobile", "API", "Hybrid"}
        if plat not in allowed_for_web:
            ui.notify(
                "Web recorder supports context platforms: Website, Touch-mobile, API, Hybrid",
                color="negative",
            )
            return
        try:
            ctx = rc.activate_context(
                BASE_DIR,
                plat,
                project,
                script,
                recorder_kind="web",
            )
        except ValueError as ex:
            ui.notify(f"Context validation failed: {ex}", color="negative")
            return

        _ui_state["recording_context"] = ctx
        _ui_state["flow_steps"] = []
        _ui_state["edit_step_idx"] = None
        _ui_state["known_pages"] = []
        _ui_state["known_names"] = []
        _ui_state["latest_names"] = []
        _ui_state["last_element"] = None

        context_platform_select.set_value(ctx["platform"]["label"])
        context_project_input.set_value(ctx["project_name"])
        context_script_input.set_value(ctx["script_name"])
        context_status_lbl.set_text(
            f"✅ Active: {ctx['platform']['label']} / {ctx['project_name']} / {ctx['script_name']}"
        )
        panel_status_lbl.set_text("✅ Context activated")

        _seed_known_from_db()
        _refresh_name_options()
        _refresh_page_options()
        _refresh_flow_select()
        _update_flow_list()
        _update_live_panel()
        _snapshot_context_state(
            setup_options={
                "start_url": (start_url_input.value or "").strip(),
                "flow_name_default": (flow_name_input.value or "").strip(),
            }
        )
        rs.log_action(
            _ui_state.get("auth_session"),
            "activate_context",
            {
                "platform": ctx["platform"]["label"],
                "project": ctx["project_name"],
                "script": ctx["script_name"],
            },
        )
        ui.notify("Recording context activated", color="positive")

    activate_context_btn.on("click", _activate_recording_context)
    if _has_context():
        c0 = _ui_state["recording_context"]
        context_status_lbl.set_text(
            f"✅ Active: {c0['platform']['label']} / {c0['project_name']} / {c0['script_name']}"
        )

    def _update_live_panel():
        live_panel.clear()
        with live_panel:
            try:
                from nlp.variable_manager import RUNTIME_VARIABLES
                var_items = list(RUNTIME_VARIABLES.items())[-3:]
                if var_items:
                    ui.label("Variables:").classes("text-xs text-indigo-400 font-semibold")
                    for k, v in reversed(var_items):
                        ui.label(f"  ${{{k}}} = {str(v)[:35]}").classes("text-xs text-green-300 font-mono")
                else:
                    ui.label("  No variables yet").classes("text-xs text-slate-500")
            except Exception:
                ui.label("  Variables unavailable").classes("text-xs text-slate-500")
            names = _ui_state.get("latest_names", [])
            if names:
                ui.label("Elements:").classes("text-xs text-indigo-400 font-semibold mt-1")
                for n in names:
                    ui.label(f"  🔖 {n}").classes("text-xs text-cyan-300 font-mono")

    def _update_flow_list():
        steps = _ui_state["flow_steps"]
        step_count_lbl.set_text(f"{len(steps)} steps")
        flow_scroll.clear()
        with flow_scroll:
            if not steps:
                ui.label("No steps yet").classes("text-slate-400 text-xs p-2")
                return
            for i, step in enumerate(steps, 1):
                is_edit = (_ui_state.get("edit_step_idx") == i - 1)
                row_bg  = "bg-yellow-900 border border-yellow-600" if is_edit else ""
                with ui.row().classes(f"step-row w-full gap-1 items-center {row_bg}"):
                    ui.label(f"{i:2d}.").classes("w-5 text-slate-400 text-xs shrink-0")
                    step_lbl = ui.label(step).classes(
                        "flex-1 text-white text-xs truncate cursor-pointer"
                        + (" text-yellow-300" if is_edit else "")
                    )
                    step_lbl.tooltip("Click to edit this step")

                    def make_edit(idx=i-1, s=step):
                        def _edit():
                            _ui_state["edit_step_idx"] = idx
                            record_btn.set_text(f"✏️ Update {idx+1}")
                            _populate_form_from_step(s)
                            _update_flow_list()
                            ui.notify(f"Editing step {idx+1}", color="info", timeout=3000)
                            rs.log_action(
                                _ui_state.get("auth_session"),
                                "edit_step_start",
                                {"index": idx, "step": s},
                            )
                        return _edit

                    def make_del(idx=i-1):
                        def _del():
                            if 0 <= idx < len(_ui_state["flow_steps"]):
                                removed_step = _ui_state["flow_steps"][idx]
                                _ui_state["flow_steps"].pop(idx)
                                if _ui_state.get("edit_step_idx") == idx:
                                    _ui_state["edit_step_idx"] = None
                                    record_btn.set_text("⚡ Add Step")
                                _update_flow_list()
                                rs.log_action(
                                    _ui_state.get("auth_session"),
                                    "delete_step",
                                    {"mode": "row_delete", "index": idx, "step": removed_step},
                                )
                        return _del

                    def make_up(idx=i-1):
                        def _up():
                            if idx > 0:
                                s_ = _ui_state["flow_steps"]
                                s_[idx-1], s_[idx] = s_[idx], s_[idx-1]
                                _update_flow_list()
                        return _up

                    def make_down(idx=i-1):
                        def _dn():
                            s_ = _ui_state["flow_steps"]
                            if idx < len(s_) - 1:
                                s_[idx], s_[idx+1] = s_[idx+1], s_[idx]
                                _update_flow_list()
                        return _dn

                    step_lbl.on("click", make_edit())
                    ui.button("▲", on_click=make_up()  ).props("flat dense").classes("text-xs text-slate-400 p-0")
                    ui.button("▼", on_click=make_down()).props("flat dense").classes("text-xs text-slate-400 p-0")
                    ui.button("✕", on_click=make_del() ).props("flat dense").classes("text-xs text-red-400 p-0")

    def _populate_form_from_step(step: str):
        """Reverse-parse a step string back into form fields for editing."""
        s  = step.strip()
        iv = bool(re.search(r'\bif\s+visible\b', s, re.I))
        if_visible_toggle.set_value(iv)
        wait_secs_input.set_visibility(iv)
        scroll_toggle.set_value(s.lower().startswith("scroll to element "))

        action_found = name_found = extra_found = None

        m = re.match(r'^click(?:\s+if\s+visible)?\s+(\S+)', s, re.I)
        if m and "double" not in s.lower():
            action_found = "click"; name_found = m.group(1)

        m2 = re.match(r'^double\s+click(?:\s+if\s+visible)?\s+(\S+)', s, re.I)
        if m2: action_found = "double click"; name_found = m2.group(1)

        m3 = re.match(r'^(?:type|fill)(?:\s+if\s+visible)?\s+"(.*?)"\s+into\s+(\S+)', s, re.I)
        if m3: action_found = "type"; extra_found = m3.group(1); name_found = m3.group(2)

        m4 = re.match(r'^verify\s+element\s+exists\s+(\S+)', s, re.I)
        if m4: action_found = "verify exists"; name_found = m4.group(1)

        m5 = re.match(r'^verify\s+text\s+"(.*?)"\s+in\s+(\S+)', s, re.I)
        if m5: action_found = "verify text"; extra_found = m5.group(1); name_found = m5.group(2)

        m6 = re.match(r'^store\s+text\s+from\s+(\S+)\s+as\s+(\S+)', s, re.I)
        if m6: action_found = "store text"; name_found = m6.group(1); extra_found = m6.group(2)

        m7 = re.match(r'^wait\s+(?:for\s+)?element\s+(\S+)', s, re.I)
        if m7: action_found = "wait for element"; name_found = m7.group(1)

        m8 = re.match(r'^scroll\s+to\s+element\s+(\S+)', s, re.I)
        if m8: action_found = "scroll to element"; name_found = m8.group(1)

        m9 = re.match(r'^hover\s+over\s+(\S+)', s, re.I)
        if m9: action_found = "hover"; name_found = m9.group(1)

        m10 = re.fullmatch(r'wait\s+(\d+(?:\.\d+)?)\s*seconds?', s, re.I)
        if m10: action_found = "wait"; extra_found = m10.group(1)

        m11 = re.match(r'^go\s+to\s+url\s+(.+)$', s, re.I)
        if m11: action_found = "go to url"; extra_found = m11.group(1).strip()

        for act, pat in [
            ("scroll down", r'^scroll\s+down'),
            ("scroll up",   r'^scroll\s+up'),
            ("press enter", r'^press\s+enter'),
        ]:
            if re.match(pat, s, re.I):
                action_found = act; break

        if action_found and action_found in action_select.options:
            action_select.set_value(action_found)
        if name_found:
            idx_m = re.match(r'^(.+?)\[([^\]]+)\]$', name_found)
            if idx_m:
                name_found = idx_m.group(1)
                idx_val    = idx_m.group(2)
                if idx_val in index_select.options:
                    index_select.set_value(idx_val)
            else:
                index_select.set_value("any")
            opts = list(name_input.options or [])
            if name_found not in opts:
                opts.append(name_found)
                name_input.options = opts
            name_input.set_value(name_found)
            name_input.update()
        if extra_found is not None:
            opts2 = list(extra_input.options or [])
            if extra_found not in opts2:
                opts2.append(extra_found)
                extra_input.options = opts2
            extra_input.set_value(extra_found)
            extra_input.update()

    def do_record():
        if not _has_context():
            ui.notify("Activate recording context first", color="negative")
            return
        raw_name   = (name_input.value or "").strip()
        name       = _sanitise(raw_name)
        action     = action_select.value
        extra      = (extra_input.value or "").strip()
        if_visible = if_visible_toggle.value
        wait_secs  = float(wait_secs_input.value or 0) if if_visible else 0
        el_index   = index_select.value
        do_scroll  = scroll_toggle.value
        page_name  = _sanitise(page_name_input.value or "") or "global_context"

        if not name and action not in ("scroll down", "scroll up", "press enter", "wait"):
            ui.notify("Enter an element name", color="negative")
            return

        step = _build_web_step(name, action, extra,
                               if_visible=if_visible, wait_secs=wait_secs,
                               el_index=el_index, scroll_first=do_scroll)
        if not step:
            ui.notify(f"Cannot build step for action: {action}", color="negative")
            return

        step_lines = [ln for ln in step.split("\n") if ln.strip()]
        op_mode = "add"

        edit_idx = _ui_state.get("edit_step_idx")
        if edit_idx is not None:
            op_mode = "update"
            if 0 <= edit_idx < len(_ui_state["flow_steps"]):
                _ui_state["flow_steps"][edit_idx:edit_idx + 1] = step_lines
            _ui_state["edit_step_idx"] = None
            record_btn.set_text("⚡ Add Step")
            ui.notify(f"✏️ Updated step {edit_idx+1}", color="info")
        else:
            _ui_state["flow_steps"].extend(step_lines)
            ui.notify(f"✅ Added: {step_lines[-1]}", color="positive")

        # Persist locator if we have extension DNA
        last = _ui_state.get("last_element")
        if last and last.get("xpath") and name:
            _persist_manual_locator(page_name, name, {
                "custom_xpath": last["xpath"],
                "tagName":      last.get("tag", ""),
                "innerText":    last.get("text", ""),
            })

        _update_flow_list()
        _refresh_name_options()
        _refresh_page_options()
        name_input.set_value(None)
        extra_input.set_value(None)
        index_select.set_value("any")
        scroll_toggle.set_value(False)
        _update_live_panel()
        _snapshot_context_state(
            setup_options={
                "last_action": action,
                "last_page": page_name,
            }
        )
        rs.log_action(
            _ui_state.get("auth_session"),
            "record_step",
            {"mode": op_mode, "action": action, "page": page_name, "element": name},
        )

    record_btn.on("click", do_record)

    # Initial render
    _update_flow_list()
    _update_live_panel()

    # Tick timer: refresh live panel + auto-fill from extension elements
    _tick_n = {"n": 0}

    async def _tick():
        _tick_n["n"] += 1
        if _has_auth_session():
            rs.touch_session(_ui_state.get("auth_session"))

        # Keep dropdowns hot with elements saved from other UI screens/processes.
        if _locator_store_changed() and _seed_known_from_db():
            _refresh_name_options()
            _refresh_page_options()

        if _tick_n["n"] % 2 == 0:
            _update_live_panel()
            _refresh_var_options()

        # Push any newly-recorded element names into the dropdown immediately
        pending = _ui_state.pop("pending_new_names", [])
        if pending:
            _refresh_name_options()
            _refresh_page_options()

        # Auto-fill Starting URL from side panel navigation
        pending_url = _ui_state.get("start_url", "")
        if pending_url and not start_url_input.value:
            start_url_input.set_value(pending_url)
            start_url_input.update()

        last = _ui_state.get("last_element")
        if last and last.get("name"):
            suggested = last["name"]
            page_hint = last.get("page", "")
            tag_hint  = last.get("tag", "")
            text_hint = last.get("text", "")

            last_el_detail.set_text(
                f"{page_hint or '?'}.{suggested}"
                + (f"  <{tag_hint}>" if tag_hint else "")
                + (f'  "{text_hint[:35]}"' if text_hint else "")
            )
            panel_status_lbl.set_text(f"✅ Received: {suggested}")

            # Always add to known_names + options so it persists in the dropdown
            if suggested not in _ui_state["known_names"]:
                _ui_state["known_names"].append(suggested)
            opts = list(name_input.options or [])
            if suggested not in opts:
                opts.append(suggested)
                name_input.options = opts
                name_input.update()
            if not name_input.value:
                name_input.set_value(suggested)
                name_input.update()

            if not page_name_input.value and page_hint:
                p_opts = list(page_name_input.options or [])
                if page_hint not in p_opts:
                    p_opts.append(page_hint)
                    page_name_input.options = p_opts
                page_name_input.set_value(page_hint)
                page_name_input.update()

            _ui_state["last_element"] = None

    ui.timer(1.0, _tick)


@ui.page("/")
def index():
    build_web_ui()


# ─── SERVER START ──────────────────────────────────────────────────────────────

if __name__ in {"__main__", "__mp_main__"}:
    # ── Auto-clear port 8080 so stale processes never block startup ──────────
    _target_port = 8080
    try:
        import subprocess as _sp, signal as _sig, os as _os, time as _t
        # Step 1: kill any stale ui_builder.py processes (except ourselves)
        _self_pid = str(os.getpid())
        _res = _sp.run(
            ["pgrep", "-f", "ui_builder.py"], capture_output=True, text=True
        )
        for _pid in _res.stdout.split():
            if _pid != _self_pid:
                try:
                    _os.kill(int(_pid), _sig.SIGKILL)
                except ProcessLookupError:
                    pass
        # Step 2: also free port directly (covers other scripts that grabbed 8080)
        _pids = _sp.check_output(
            ["lsof", "-ti", f"tcp:{_target_port}"], text=True
        ).split()
        for _pid in _pids:
            if _pid != _self_pid:
                try:
                    _os.kill(int(_pid), _sig.SIGKILL)
                except ProcessLookupError:
                    pass
        if _pids:
            _t.sleep(0.5)
            print(f"⚠️  Cleared stale process(es) on port {_target_port}: {', '.join(_pids)}")
    except subprocess.CalledProcessError:
        pass   # no process was using the port — normal case

    print("""
╔══════════════════════════════════════════════════╗
║   🌐  Web Element Recorder                      ║
║   DevTools-style layout                         ║
║   Open → http://localhost:8080                  ║
╚══════════════════════════════════════════════════╝
""")
    ui.run(
        title="Web Recorder",
        port=_target_port,
        reload=False,
        show=False,
        dark=True,
    )
