import os
import json
import re
import logging
import shutil
import threading
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from nicegui import app, ui, Client

# Framework Imports (Ensure these files exist in your directory)
import execution.action_service as actions
from registry import ACTION_REGISTRY
from locators.manager import get_all_locators

# =====================================================
# 1. ENTERPRISE CONFIGURATION
# =====================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "recorded_elements.json")

db_lock = threading.Lock()

# ARCHITECTURAL FIX: Restored the global memory array
test_script_payload = []


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
    if attrs.get("aria-label"):
        return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"):
        return f"//{tag}[@title='{attrs['title']}']"

    classes = attrs.get("class", "")
    if classes:
        valid_classes = [c for c in classes.split() if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join([f"contains(@class,'{c}')" for c in valid_classes])
            return f"//{tag}[{contains_logic}]"

    text = element_dna.get("innerText")
    if text and len(text) < 40:
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
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        logging.error("Database corrupted → quarantining")
        backup_path = DB_FILE + ".corrupt.bak"
        try:
            shutil.copy2(DB_FILE, backup_path)
            logging.info(f"Backup created → {backup_path}")
        except Exception:
            logging.exception("Backup failed")
        return {}
    except Exception:
        # ARCHITECTURAL FIX: Catch-all prevents OS-level file lock crashes
        logging.exception("Database read failure")
        return {}


def write_database_unlocked(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
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

        return safe_locator_name, data[safe_page_name][safe_locator_name]
    

# =====================================================
# 5. API ROUTES & LIFECYCLE
# =====================================================

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
    try:
        raw_dna = await request.json()
    except Exception:
        logging.exception("Invalid JSON payload")
        return {"status": "error", "message": "Invalid JSON"}

    try:
        locator_name, processed_dna = persist_element_to_disk(raw_dna)

        step_payload = {
            "action": "Click Element",
            "parameters": {
                "locator": locator_name,
                "locator_data": processed_dna
            }
        }

        test_script_payload.append(step_payload)
        
        logging.info(f"Recorded element → {locator_name}")

        # ARCHITECTURAL FIX: Broadcast the update to all connected WebSockets natively
        for client in Client.instances.values():
            with client:
                render_script_builder.refresh()

        return {"status": "success"}
    except Exception:
        logging.exception("Recording failure")
        return {"status": "error"}


# =====================================================
# 6. UI BOOTSTRAP & SCRIPT BUILDER
# =====================================================

# =====================================================
# 6. UI BOOTSTRAP & SCRIPT BUILDER
# =====================================================

with ui.card().classes("mb-4 p-4 bg-gray-100 border-l-4 border-purple-500"):
    ui.label("Credits & Technologies Used").classes("text-xl font-bold text-purple-700 mb-2")
    ui.markdown("""
**AI & Frameworks:**
- OpenAI GPT (Copilot, LLMs)
- NiceGUI (UI Framework)
- Playwright (Browser Automation)
- FastAPI (API Backend)
- Custom NLP & Healer Engines
- Python 3.x
""").classes("text-sm text-gray-700")
ui.label("Codeless Script Builder").classes("text-3xl font-bold mb-6")

# --- MANUAL STEP DIALOG ENGINE ---
with ui.dialog() as manual_step_dialog, ui.card().classes('w-96 border-2 border-green-500'):
    ui.label('Add Manual Action').classes('text-xl font-bold text-green-700 mb-2')
    
    action_dropdown = ui.select(
        ['Go To URL', 'Type Text', 'Press Key', 'Wait (Seconds)', 'Click Element'], 
        value='Type Text', label='Action Type'
    ).classes('w-full mb-2')
    
    
    manual_locator_input = ui.input('Target Locator (Optional)').classes('w-full mb-2').tooltip("Leave blank for URL or Wait actions")
    action_data_input = ui.input('Input Value / Data').classes('w-full mb-4')
    
    def save_manual_step():
        if not action_data_input.value and action_dropdown.value != 'Click Element':
            ui.notify("Input Value cannot be empty for this action!", color="red")
            return
            
        step = {
            "action": action_dropdown.value,
            "parameters": {
                "locator": manual_locator_input.value or "N/A",
                "data": action_data_input.value or ""
            }
        }
        test_script_payload.append(step)
        render_script_builder.refresh()
        manual_step_dialog.close()
        
        # Reset inputs for next use
        manual_locator_input.value = ""
        action_data_input.value = ""
        ui.notify(f"Manual step added: {step['action']}", color="green")
        
        
    with ui.row().classes('w-full justify-end'):
        ui.button('Cancel', color='gray', on_click=manual_step_dialog.close)
        ui.button('Add Step', color='green', on_click=save_manual_step)


@ui.refreshable
def render_script_builder():
    """
    Dynamically renders the test_script_payload array into a visual list.
    """
    with ui.column().classes("w-full max-w-4xl mx-auto border p-4 bg-gray-50 rounded shadow-inner"):
        
        # 1. Empty State Handling
        if not test_script_payload:
            ui.label("No steps recorded yet. Alt+Click (Windows) or option+click(MAC) an element in the browser to start...").classes("text-gray-500 italic py-8 text-center w-full")
            return

        # 2. Render Existing Steps
        for index, step in enumerate(test_script_payload):
            with ui.card().classes("w-full flex flex-row items-center justify-between p-3 mb-2 border-l-4 border-blue-500"):
                with ui.row().classes("items-center gap-2"):
                    ui.badge(f"{index + 1}", color="blue")
                    ui.label(step.get("action", "Unknown Action")).classes("font-bold text-lg")
                
                parameters = step.get("parameters") or {}
                locator_name = parameters.get("locator", "N/A")
                action_data = parameters.get("data", "")
                
                with ui.column().classes("items-end"):
                    ui.label(locator_name).classes("font-mono text-sm bg-gray-200 px-2 py-1 rounded text-gray-700")
                    if action_data:
                        ui.label(f"Data: {action_data}").classes("text-xs text-gray-500 italic")

# --- SCRIPT CONTROLS ---
with ui.row().classes("w-full max-w-4xl mx-auto mt-4 justify-between"):
    with ui.row().classes("gap-2"):
        ui.button("➕ Add Manual Step", color="green", on_click=manual_step_dialog.open)
        ui.button("▶️ Run Playwright Script", color="primary", on_click=lambda: ui.notify("Execution engine not wired yet!"))
last_payload_count = {"value": 0}

def sync_ui():

        if last_payload_count["value"] != len(test_script_payload):

            render_script_builder.refresh()

        last_payload_count["value"] = len(test_script_payload)

        ui.timer(1.0, sync_ui)        
def clear_script():
        test_script_payload.clear()
        render_script_builder.refresh()
        ui.notify("Script cleared.")

        ui.button("🗑️ Clear Script", color="red", on_click=clear_script).classes("ml-auto")       

# 3. Initial Draw
render_script_builder()


# =====================================================
# 7. SERVER START
# =====================================================

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        title="Internal Automation Tool",
        port=8080,
        reload=False, # Keep this False to prevent ghost workers
        show=False    # Prevents opening a new browser tab on every single restart
    )