"""
nlp/variable_manager.py
Unified variable/memory management for both NLP-flow and JSON-flow execution paths.

- RUNTIME_VARIABLES: module-level dict replacing scattered globals in actions.py.
- resolve_variables(): NLP-flow inline ${var} interpolation (from actions.py).
- VariableManager: class-based strict-mode resolver for JSON-flow execution (from runner.py).
"""
import re
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SHARED RUNTIME MEMORY (replaces global RUNTIME_VARIABLES in actions.py)
# ─────────────────────────────────────────────────────────────────────────────
RUNTIME_VARIABLES: Dict[str, Any] = {}


def resolve_variables(text: Any) -> Any:
    """
    THE INTERPOLATION ENGINE (NLP-flow path)
    1. If raw text exactly matches a stored variable name, return its value.
    2. Scans for ${var_name} syntax for inline string replacement.
    """
    if not isinstance(text, str):
        return text

    clean_text = text.strip()

    # Exact-match fallback: typed variable name without ${}
    if clean_text in RUNTIME_VARIABLES:
        return str(RUNTIME_VARIABLES[clean_text])

    # Standard ${var} interpolation
    matches = re.findall(r'\$\{([^}]+)\}', text)
    result = text
    for var_name in matches:
        if var_name in RUNTIME_VARIABLES:
            result = result.replace(f"${{{var_name}}}", str(RUNTIME_VARIABLES[var_name]))
        else:
            raise ValueError(
                f"❌ Execution Error: Variable '${{{var_name}}}' is not stored in memory!"
            )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# STRICT CLASS-BASED MANAGER (JSON-flow path)
# ─────────────────────────────────────────────────────────────────────────────
class VariableManager:
    """
    Isolates runtime memory per test execution and enforces strict dependency
    resolution to prevent silent test failures (fail-fast).
    """

    def __init__(self, strict_mode: bool = True):
        self.memory: Dict[str, Any] = {}
        self.strict_mode = strict_mode

    def save(self, raw_name: str, value: Any) -> None:
        """Sanitizes the variable name and saves it to runtime RAM."""
        if not raw_name:
            return
        clean_name = re.sub(r'[^a-zA-Z0-9_]', '', str(raw_name))
        self.memory[clean_name] = value
        logger.info(f"🧠 MEMORY SECURED: ${{{clean_name}}} = '{value}'")

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
                        logger.info(f"🔄 RESOLVED: ${{{var_name}}} → '{actual_value}'")
                    else:
                        error_msg = f"Variable '${{{var_name}}}' was not found in runtime memory!"
                        if self.strict_mode:
                            logger.error(f"❌ FATAL MEMORY MISS: {error_msg}")
                            raise ValueError(error_msg)
                        else:
                            logger.warning(f"⚠️ MEMORY MISS (Ignoring): {error_msg}")

                resolved_params[key] = resolved_value
            else:
                resolved_params[key] = value

        return resolved_params
