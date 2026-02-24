import re
import os
import json
import inspect
import asyncio
import logging
import threading
from nicegui import app, ui
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

# Framework Imports
import actions 
from registry import ACTION_REGISTRY 
from locator_utils import get_all_locators 

logging.basicConfig(level=logging.INFO, format='%(message)s')

# ==========================================
# 1. GLOBAL STATE & OS-LEVEL PATHING
# ==========================================
test_script_payload = []

# ARCHITECTURAL UPGRADE: Absolute Pathing. 
# This guarantees the file is strictly saved in the exact same folder as ui_builder.py,
# regardless of where your terminal was opened from.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "recorded_elements.json")

db_lock = threading.Lock()

# ... (Keep your existing DATA SANITIZATION ENGINE here) ...
def sanitize_and_match_identifier(raw_name: str, existing_keys: list) -> str:
    clean_name = re.sub(r'[\s\-]+', '_', raw_name.strip().lower())
    clean_name = re.sub(r'[^a-z0-9_]', '', clean_name)
    base_pattern = clean_name.replace('_', '')
    for existing_key in existing_keys:
        if base_pattern == existing_key.replace('_', ''): return existing_key
    return clean_name

def generate_safe_xpath(element_dna):
    tag = element_dna.get("tagName", "*")
    attrs = element_dna.get("attributes", {})
    if attrs.get("id"): return f"//{tag}[@id='{attrs['id']}']"
    if attrs.get("name"): return f"//{tag}[@name='{attrs['name']}']"
    if attrs.get("aria-label"): return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"): return f"//{tag}[@title='{attrs['title']}']"
    classes = attrs.get("class", "")
    if classes:
        valid_classes = [c for c in classes.split() if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join([f"contains(@class, '{c}')" for c in valid_classes])
            return f"//{tag}[{contains_logic}]"
    text = element_dna.get("innerText")
    if text and len(text) < 40:
        if "'" in text and '"' not in text: return f'//{tag}[normalize-space(text())="{text}"]'
        elif '"' in text and "'" not in text: return f"//{tag}[normalize-space(text())='{text}']"
        elif "'" in text and '"' in text: return f"text={text}"
        else: return f"//{tag}[normalize-space(text())='{text}']"
    return f"//{tag}"

# ==========================================
# 2. DATA SANITIZATION ENGINE
# ==========================================
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
    
    if attrs.get("id"): return f"//{tag}[@id='{attrs['id']}']"
    if attrs.get("name"): return f"//{tag}[@name='{attrs['name']}']"
    if attrs.get("aria-label"): return f"//{tag}[@aria-label='{attrs['aria-label']}']"
    if attrs.get("title"): return f"//{tag}[@title='{attrs['title']}']"
    
    classes = attrs.get("class", "")
    if classes:
        valid_classes = [c for c in classes.split() if "font" not in c.lower()]
        if valid_classes:
            contains_logic = " and ".join([f"contains(@class, '{c}')" for c in valid_classes])
            return f"//{tag}[{contains_logic}]"
            
    text = element_dna.get("innerText")
    if text and len(text) < 40:
        if "'" in text and '"' not in text: return f'//{tag}[normalize-space(text())="{text}"]'
        elif '"' in text and "'" not in text: return f"//{tag}[normalize-space(text())='{text}']"
        elif "'" in text and '"' in text: return f"text={text}"
        else: return f"//{tag}[normalize-space(text())='{text}']"
            
    return f"//{tag}"

# ==========================================
# 3. DISK I/O MANAGER (Atomic Writes & Truth in Logging)
# ==========================================
def persist_element_to_disk(element_dna):
    with db_lock:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r", encoding='utf-8') as f: data = json.load(f)
            except json.JSONDecodeError: data = {} 
        else: data = {}

        raw_page = element_dna.pop("userPageName", "global_context")
        raw_locator = element_dna.pop("userLocatorName", "unnamed_locator")

        safe_page_name = sanitize_and_match_identifier(raw_page, list(data.keys()))
        if safe_page_name not in data: data[safe_page_name] = {}

        existing_locators = list(data[safe_page_name].keys())
        safe_locator_name = sanitize_and_match_identifier(raw_locator, existing_locators)

        element_dna["custom_xpath"] = generate_safe_xpath(element_dna)

        # 1. Modify the dictionary in RAM
        is_update = safe_locator_name in data[safe_page_name]
        if is_update:
            data[safe_page_name][safe_locator_name].update(element_dna)
        else:
            data[safe_page_name][safe_locator_name] = element_dna

        # 2. PERFORM THE HARDWARE WRITE FIRST
        with open(DB_FILE, "w", encoding='utf-8') as f:
            json.dump(data, f, indent=2)
            f.flush() 
            os.fsync(f.fileno()) 

        # 3. LOG SUCCESS SECOND (Truth in Logging)
        if is_update:
            logging.info(f"🔄 SUCCESS: Merged updates into {safe_page_name} -> {safe_locator_name}")
        else:
            logging.info(f"💾 SUCCESS: Saved new locator {safe_page_name} -> {safe_locator_name}")

        return safe_locator_name, data[safe_page_name][safe_locator_name]

# ==========================================
# 4. API & MIDDLEWARE
# ==========================================
middleware_already_added = any(m.cls == CORSMiddleware for m in app.user_middleware)
if not middleware_already_added:
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.post('/api/record-element')
async def receive_recorded_element(request: Request):
    try:
        raw_dna = await request.json()
        locator_name, processed_dna = persist_element_to_disk(raw_dna)
        step_payload = {"action": "Click Element", "parameters": {"locator": locator_name, "locator_data": processed_dna}}
        test_script_payload.append(step_payload)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get('/api/get-database-schema')
async def get_database_schema():
    """Returns the existing pages and locators for the Extension UI dropdowns."""
    with db_lock:
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r", encoding='utf-8') as f: data = json.load(f)
                return {page: list(locs.keys()) for page, locs in data.items()}
            except Exception: return {}
        return {}

# ==========================================
# 5. FRONTEND ARCHITECTURE
# ==========================================
@ui.page('/')
def main_builder_ui():
    selected_action = {"name": None, "function": None}
    dynamic_inputs = {}

    def get_available_variables():
        discovered_vars = set()
        for step in test_script_payload:
            for key, value in step.get("parameters", {}).items():
                if 'save_to_variable_name' in key and value:
                    discovered_vars.add(f"${{{re.sub(r'[^a-zA-Z0-9_]', '', str(value))}}}")
        return list(discovered_vars)

    def on_dropdown_change(event):
        selected_action["name"] = event.value
        selected_action["function"] = ACTION_REGISTRY[event.value]
        fresh_locators = get_all_locators()
        input_container.clear()
        dynamic_inputs.clear()
        sig = inspect.signature(selected_action["function"])
        live_variables = get_available_variables()
        
        with input_container:
            for param_name in sig.parameters:
                if param_name == 'page': continue
                if 'locator' in param_name.lower() and fresh_locators:
                    dynamic_inputs[param_name] = ui.select(label=f"Select {param_name}", options=fresh_locators, with_input=True).classes('w-full mb-2')
                elif param_name == 'state':
                    dynamic_inputs[param_name] = ui.select(label="Element State", options=['visible', 'hidden', 'attached', 'detached'], value='visible', with_input=True).classes('w-full mb-2')
                elif 'ignore_case' in param_name.lower() or 'true_false' in param_name.lower():
                    dynamic_inputs[param_name] = ui.select(label=param_name.replace("_", " ").title(), options=['False', 'True'], value='False', with_input=True).classes('w-full mb-2')
                else:
                    if 'save_to_variable_name' in param_name: dynamic_inputs[param_name] = ui.input(label="Variable Name (e.g., price)").classes('w-full mb-2')
                    else: dynamic_inputs[param_name] = ui.input(label=param_name.replace("_", " ").title(), autocomplete=live_variables).classes('w-full mb-2')

    def add_step():
        if not selected_action["name"]: return ui.notify('Please select an action!', type='warning')
        step_data = {}
        for param_name, input_element in dynamic_inputs.items():
            step_data[param_name] = input_element.value
            if not step_data[param_name]: return ui.notify(f'Parameter "{param_name}" cannot be empty!', type='negative')
        test_script_payload.append({"action": selected_action['name'], "parameters": step_data})
        test_flow_ui.refresh()
        ui.notify(f"Added {selected_action['name']}", type='positive')

    def remove_step(index):
        if 0 <= index < len(test_script_payload):
            test_script_payload.pop(index)
            test_flow_ui.refresh()

    def save_test_flow(file_path='.flow_files/custom_test.json'):
        if not test_script_payload: return ui.notify('No steps to save!', type='negative')
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f: json.dump(test_script_payload, f, indent=4)
        ui.notify('Test Flow Saved!', type='positive')
        return True

    async def execute_test_live():
        if not save_test_flow(): return
        execution_log_ui.clear()
        execution_log_ui.push("🚀 Spawning execution process...")
        run_btn.disable()
        try:
            process = await asyncio.create_subprocess_exec("python3", "runner.py", '.flow_files/custom_test.json', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            while True:
                line = await process.stdout.readline()
                if not line: break 
                if line.decode('utf-8').strip(): execution_log_ui.push(line.decode('utf-8').strip())
            await process.wait()
            ui.notify("Test Passed!" if process.returncode == 0 else "Test Failed!", type='positive' if process.returncode == 0 else 'negative')
        except Exception as e: execution_log_ui.push(f"⚠️ Error: {e}")
        finally: run_btn.enable()

    @ui.refreshable
    def test_flow_ui():
        if not test_script_payload: return ui.label("Queue is empty.").classes("text-gray-400 italic mt-4")
        for index, step in enumerate(test_script_payload):
            card_color = 'bg-green-50 border-green-200' if "locator_data" in step.get("parameters", {}) else 'bg-gray-50 border-gray-200'
            with ui.card().classes(f'w-full p-4 mb-2 border flex flex-row justify-between items-center {card_color}'):
                with ui.column().classes('gap-0'):
                    ui.label(f"{index + 1}. {step['action']}").classes('text-lg font-bold text-blue-700')
                    for key, val in step['parameters'].items():
                        if key != "locator_data": ui.label(f"{key}: {val}").classes('text-sm text-gray-600')
                ui.button('🗑️', color='red', on_click=lambda i=index: remove_step(i)).classes('px-3 py-1 text-xl')

    ui.label('Codeless Automation Builder').classes('text-3xl font-bold mb-6 text-blue-600')
    with ui.row().classes('w-full items-start gap-4 mb-6'):
        with ui.column().classes('w-1/3'):
            ui.select(label="1. Search & Choose Action", options=list(ACTION_REGISTRY.keys()), on_change=on_dropdown_change, with_input=True).classes('w-full mb-4')
            input_container = ui.column().classes('w-full')
            ui.button('2. Add Step to Flow', on_click=add_step).classes('w-full mt-4')
        with ui.column().classes('w-1/3 px-4 border-l border-r border-gray-300'):
            ui.label('Test Flow Queue').classes('text-xl font-bold mb-4')
            with ui.column().classes('w-full min-h-[200px]'): test_flow_ui() 
            with ui.row().classes('w-full mt-6 gap-2'):
                ui.button('💾 Save Flow', on_click=save_test_flow).classes('flex-1 bg-green-600 text-white')
                run_btn = ui.button('▶️ Run Test', on_click=execute_test_live).classes('flex-1 bg-blue-600 text-white font-bold')
        with ui.column().classes('w-1/3'):
            ui.label('Live Execution Terminal').classes('text-xl font-bold mb-4')
            execution_log_ui = ui.log(max_lines=500).classes('w-full h-96 bg-gray-900 text-green-400 font-mono text-sm p-2 rounded shadow-inner')

    def sync_state():
        if len(test_script_payload) != getattr(sync_state, 'last_count', -1):
            test_flow_ui.refresh()
            sync_state.last_count = len(test_script_payload)
    sync_state.last_count = len(test_script_payload)
    ui.timer(1.0, sync_state)

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Internal Automation Tool", port=8080, reload=False, storage_secret="dev_secret_key")