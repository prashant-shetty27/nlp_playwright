import os
import json
import logging
import actions
from registry import ACTION_REGISTRY
from actions import open_browser, close_browser, resolve_variables # <-- Import the resolver

logging.basicConfig(level=logging.INFO, format='%(message)s')

def execute_json_flow(json_path: str):
    if not os.path.exists(json_path):
        logging.error(f"❌ Error: File '{json_path}' not found.")
        return

    logging.info(f"📂 Loading Test Flow: {json_path}")
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            test_steps = json.load(f)
    except Exception as e:
        logging.error(f"❌ Failed to parse JSON: {e}")
        return

    page = open_browser()
    
    try:
        for index, step in enumerate(test_steps):
            action_name = step.get("action")
            params = step.get("parameters", {})
            
            logging.info(f"▶️ Executing Step {index + 1}: [{action_name}]")
            target_function = ACTION_REGISTRY.get(action_name)
            
            if not target_function:
                raise ValueError(f"Architecture Error: '{action_name}' is not registered.")
            
            # ==========================================
            # THE VARIABLE INTERCEPTOR
            # Every parameter is scanned and replaced with live memory data
            # before it ever touches Playwright.
            # ==========================================
            resolved_params = {}
            for key, val in params.items():
                if isinstance(val, str):
                    resolved_params[key] = resolve_variables(val)
                else:
                    resolved_params[key] = val
                    
            # Execute the function using the resolved, injected data
            target_function(page=page, **resolved_params)
            
        logging.info("✅ Test Flow Executed Successfully!")
        
    except Exception as e:
        logging.error(f"❌ Test Failed at Step {index + 1} [{action_name}]: {e}")
        
    finally:
        close_browser(page, test_name="codeless_run")

if __name__ == "__main__":
    # Fallback for manual terminal execution
    execute_json_flow('.flow_files/custom_justdial_test.json')