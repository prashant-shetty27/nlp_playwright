import os
import json
import re
import logging
from typing import Any, Dict # ARCHITECTURAL UPGRADE 3: Type Hinting

from registry import ACTION_REGISTRY
from actions import open_browser, close_browser

logging.basicConfig(level=logging.INFO, format='%(message)s')

class VariableManager:
    """
    Architectural Impact: Isolates runtime memory per test execution and 
    enforces strict dependency resolution to prevent silent test failures.
    """
    def __init__(self, strict_mode: bool = True):
        self.memory: Dict[str, Any] = {}
        # ARCHITECTURAL UPGRADE 1: Strict Mode Toggle
        self.strict_mode = strict_mode 

    def save(self, raw_name: str, value: Any) -> None:
        """Sanitizes the variable name and saves it to runtime RAM."""
        if not raw_name:
            return
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '', str(raw_name))
        self.memory[clean_name] = value
        logging.info(f"🧠 MEMORY SECURED: ${{{clean_name}}} = '{value}'")

    def resolve_parameters(self, step_parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Intercepts step parameters BEFORE Playwright sees them.
        Swaps any ${var} strings with their actual live values.
        """
        resolved_params: Dict[str, Any] = {}
        
        for key, value in step_parameters.items():
            if isinstance(value, str) and '${' in value:
                resolved_value = value
                matches = re.findall(r'\$\{([^}]+)\}', value)
                
                for var_name in matches:
                    if var_name in self.memory:
                        actual_value = str(self.memory[var_name])
                        resolved_value = resolved_value.replace(f'${{{var_name}}}', actual_value)
                        
                        # ARCHITECTURAL UPGRADE 2: Audit Logging for Debugging
                        logging.info(f"🔄 RESOLVED: ${{{var_name}}} → '{actual_value}'")
                    else:
                        error_msg = f"Variable '${{{var_name}}}' was not found in runtime memory!"
                        
                        # ARCHITECTURAL UPGRADE 1: The "Fail Fast" Mechanism
                        if self.strict_mode:
                            logging.error(f"❌ FATAL MEMORY MISS: {error_msg}")
                            raise ValueError(error_msg) # This violently halts the test
                        else:
                            logging.warning(f"⚠️ MEMORY MISS (Ignoring): {error_msg}")
                
                resolved_params[key] = resolved_value
            else:
                resolved_params[key] = value
                
        return resolved_params


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

    # Instantiate memory in Strict Mode
    runtime_memory = VariableManager(strict_mode=True)
    page = open_browser()
    
    try:
        for index, step in enumerate(test_steps):
            action_name = step.get("action")
            raw_params = step.get("parameters", {})
            
            logging.info(f"▶️ Executing Step {index + 1}: [{action_name}]")
            target_function = ACTION_REGISTRY.get(action_name)
            
            if not target_function:
                raise ValueError(f"Architecture Error: '{action_name}' is not registered.")
            
            # This line will now throw a fatal error if a variable is missing
            resolved_params = runtime_memory.resolve_parameters(raw_params)
            
            save_target = resolved_params.pop("save_to_variable_name", None)
            
            # Execute the function using the resolved, injected data
            step_result = target_function(page=page, **resolved_params)
            
            # Memory Injection
            if save_target and step_result is not None:
                runtime_memory.save(save_target, step_result)
            
        logging.info("✅ Test Flow Executed Successfully!")
        
    except Exception as e:
        # The fatal memory error will be gracefully caught and logged right here
        logging.error(f"❌ Test Failed at Step {index + 1} [{action_name}]: {e}")
        
    finally:
        close_browser(page, test_name="codeless_run")

if __name__ == "__main__":
    execute_json_flow('.flow_files/custom_test.json')