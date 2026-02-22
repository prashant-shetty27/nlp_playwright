from nicegui import ui
import inspect
import json
import asyncio
import logging
import os


from registry import ACTION_REGISTRY
import actions
from runner import execute_json_flow
from locator_utils import get_all_locators

# ==========================================
# 1. DATA LAYER: LOCATORS
# ==========================================


AVAILABLE_LOCATORS = get_all_locators()

# ==========================================
# 2. STATE MANAGEMENT
# ==========================================

selected_action = {"name": None, "function": None}
dynamic_inputs = {}
test_script_payload = []
execution_log_ui = None

# ==========================================
# 3. LIVE LOGGING ARCHITECTURE
# ==========================================

class WebUILogHandler(logging.Handler):
    def emit(self, record):
        if record.name.startswith(('nicegui', 'uvicorn', 'fastapi', 'asyncio', 'watchfiles')):
            return
        try:
            log_msg = self.format(record)
            if execution_log_ui:
                execution_log_ui.push(log_msg)
        except Exception:
            pass

web_logger = WebUILogHandler()
web_logger.setFormatter(logging.Formatter('%(message)s'))
logging.getLogger().addHandler(web_logger)
logging.getLogger().setLevel(logging.INFO)

# ==========================================
# 4. UI LOGIC & INTROSPECTION
# ==========================================

def on_dropdown_change(event):
    selected_action["name"] = event.value
    selected_action["function"] = ACTION_REGISTRY[event.value]
    
    # --- LIVE RELOAD ARCHITECTURE ---
    # Fetches fresh data from the hard drive every time you select an action
    fresh_locators = get_all_locators()
    
    input_container.clear()
    dynamic_inputs.clear()

    sig = inspect.signature(selected_action["function"])
    
    with input_container:
        for param_name in sig.parameters:
            if param_name == 'page':
                continue
            
            # 1. Catch Locators
            if 'locator' in param_name.lower() and fresh_locators:
                dynamic_inputs[param_name] = ui.select(
                    label=f"Search & Select {param_name}", 
                    options=fresh_locators,
                    with_input=True
                ).classes('w-full mb-2')
                
            # 2. Catch State dropdowns
            elif param_name == 'state':
                dynamic_inputs[param_name] = ui.select(
                    label="Element State",
                    options=['visible', 'hidden', 'attached', 'detached'],
                    value='visible',
                    with_input=True
                ).classes('w-full mb-2')
                
            # 3. Catch our new Boolean Ignore Case parameters
            elif 'ignore_case' in param_name.lower() or 'true_false' in param_name.lower():
                dynamic_inputs[param_name] = ui.select(
                    label=param_name.replace("_", " ").title(),
                    options=['False', 'True'],
                    value='False',
                    with_input=True
                ).classes('w-full mb-2')
                
            # 4. Fallback to standard Text Input
            else:
                dynamic_inputs[param_name] = ui.input(
                    label=param_name.replace("_", " ").title()
                ).classes('w-full mb-2')

def add_step():
    if not selected_action["name"]:
        ui.notify('Please select an action first!', type='warning')
        return

    step_data = {}
    for param_name, input_element in dynamic_inputs.items():
        step_data[param_name] = input_element.value
        if not step_data[param_name]:
            ui.notify(f'Parameter "{param_name}" cannot be empty!', type='negative')
            return

    step_payload = {
        "action": selected_action['name'],
        "parameters": step_data
    }
    test_script_payload.append(step_payload)

    with test_flow_container:
        with ui.card().classes('w-full p-4 mb-2 bg-gray-50 border border-gray-200 shadow-sm'):
            ui.label(selected_action['name']).classes('text-lg font-bold text-blue-700')
            for key, val in step_data.items():
                ui.label(f"{key}: {val}").classes('text-sm text-gray-600')
    
    ui.notify(f"Added {selected_action['name']} to flow!", type='positive')

def save_test_flow():
    if not test_script_payload:
        ui.notify('No steps to save!', type='negative')
        return
        
    os.makedirs('.flow_files', exist_ok=True)
    file_path = '.flow_files/custom_justdial_test.json'
    with open(file_path, 'w') as f:
        json.dump(test_script_payload, f, indent=4)
        
    ui.notify('Test Flow Saved Successfully!', type='positive')

async def execute_test_live():
    file_path = '.flow_files/custom_justdial_test.json'
    if not os.path.exists(file_path):
        ui.notify("Please save the test flow first!", type='negative')
        return

    ui.notify("Starting Test Execution...", type='info')
    if execution_log_ui:
        execution_log_ui.clear() 
    
    try:
        await asyncio.to_thread(execute_json_flow, file_path)
        ui.notify("Test Execution Completed!", type='positive')
    except Exception as e:
        ui.notify(f"Test Execution Failed: {e}", type='negative')

# ==========================================
# 5. UI LAYOUT (THE VISUAL PRESENTATION)
# ==========================================

ui.label('Codeless Automation Builder').classes('text-3xl font-bold mb-6 text-blue-600')

with ui.row().classes('w-full items-start gap-4 mb-6'):
    
    # Left Column: The Test Builder
    with ui.column().classes('w-1/3'):
        ui.select(
            label="1. Search & Choose Action", 
            options=list(ACTION_REGISTRY.keys()), 
            on_change=on_dropdown_change,
            with_input=True
        ).classes('w-full mb-4')
        
        input_container = ui.column().classes('w-full')
        ui.button('2. Add Step to Flow', on_click=add_step).classes('w-full mt-4')

    # Middle Column: The Execution Queue
    with ui.column().classes('w-1/3 px-4 border-l border-r border-gray-300'):
        ui.label('Test Flow Queue').classes('text-xl font-bold mb-4')
        test_flow_container = ui.column().classes('w-full min-h-[200px]')
        
        with ui.row().classes('w-full mt-6 gap-2'):
            ui.button('💾 Save Flow', on_click=save_test_flow).classes('flex-1 bg-green-600 text-white')
            ui.button('▶️ Run Test', on_click=execute_test_live).classes('flex-1 bg-blue-600 text-white font-bold')

    # Right Column: The Live Execution Logs
    with ui.column().classes('w-1/3'):
        ui.label('Live Execution Terminal').classes('text-xl font-bold mb-4')
        execution_log_ui = ui.log(max_lines=50).classes('w-full h-96 bg-gray-900 text-green-400 font-mono text-sm p-2 rounded shadow-inner')

ui.run(title="Internal Automation Tool")