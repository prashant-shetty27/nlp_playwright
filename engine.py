import os
import sys
import re
import json
import logging

# Local Framework Modules
import actions
from clean_locators import sanitize_database
from sync_snippets import sync_locators_to_snippets 
from command_parser import parse_step

logger = logging.getLogger(__name__)

# --- GLOBAL STATE ---
VARIABLES = {}
STOP_ON_FAILURE = False
PAGE = None

def load_run_config():
    """Reads optional run settings from config.json."""
    path = "config.json"
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return data.get("run", {})
        except Exception as e:
            logger.warning("⚠️ Failed to load config.json: %s", e)
    return {}

# =====================================================
# STRUCTURED COMMAND EXECUTION ROUTER
# =====================================================
def execute_step_from_command(cmd):
    """Routes the parsed command object to the appropriate Action function."""
    global PAGE
    
    if cmd.type == "open":
        actions.open_site(PAGE, cmd.target)
    elif cmd.type == "search":
        actions.search(PAGE, cmd.text)
    elif cmd.type == "wait":
        actions.wait_seconds(PAGE, cmd.wait)
    elif cmd.type == "wait_for_result_page_load":
        actions.wait_for_result_page_load(PAGE)
    elif cmd.type == "refresh":
        actions.refresh_page(PAGE)    
    elif cmd.type == "scroll_until_text_visible":
        actions.scroll_until_text_visible(PAGE, cmd.text, cmd.count, cmd.wait)
    elif cmd.type == "verify_text":
        actions.verify_exact_text(PAGE, cmd.text, cmd.count)
    elif cmd.type == "verify_image":
        threshold = getattr(cmd, 'threshold', 0.5)
        if not actions.verify_image_on_page(PAGE, cmd.image_path, threshold=threshold):
            raise Exception(f"❌ Image verification failed for {cmd.image_path}")
    elif cmd.type == "click":
        actions.click_element(PAGE, cmd.target)
    elif cmd.type == "fill":
        actions.fill_element(PAGE, cmd.text, cmd.target)
    else:
        raise ValueError(f"❌ Unknown command type: {cmd.type}")

# =====================================================
# CORE INTERPRETER (Pre-processor & Parser)
# =====================================================
def interpret(step):
    """Pre-processes variables and parses natural language into commands."""
    normalized = step.strip()
    logger.info("👉 Interpreting: %s", normalized)

    # 1. VARIABLE STORAGE HANDLER
    # "store 'value' as my_var"
    if "store " in normalized.lower() and " as " in normalized.lower():
        pattern = r"store\s+\"(.*?)\"\s+as\s+(.*)"
        match = re.search(pattern, step, re.IGNORECASE)
        if match:
            val, var_name = match.groups()
            VARIABLES[var_name.strip()] = val.strip()
            logger.info("📦 Variable Stored: %s = %s", var_name, val)
            return

    # 2. VARIABLE INJECTION (Pre-processing)
    # Replaces {my_var} with the actual stored value before parsing
    for var_name, val in VARIABLES.items():
        placeholder = f"{{{var_name}}}"
        if placeholder in step:
            step = step.replace(placeholder, val)

    # 3. SYNTAX PARSING
    try:
        cmd = parse_step(step)
    except ValueError as e:
        # Strictly catch syntax errors from the parser
        raise ValueError(f"❌ Invalid syntax or unknown command: {step}") from e

    # 4. EXECUTION
    # Any exception raised here (like Playwright timeouts) will naturally bubble up to run_steps
    execute_step_from_command(cmd)

# =====================================================
# EXECUTION ENGINE LIFECYCLE
# =====================================================
def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        force=True 
    )

def run_steps(file_path):
    setup_logging()
    stats = {"passed": 0, "failed": 0, "log": []}
    
    # Pre-flight checks
    sanitize_database()
    run_cfg = load_run_config()
    
    global STOP_ON_FAILURE, PAGE
    STOP_ON_FAILURE = bool(run_cfg.get("stop_on_failure", False))
    PAGE = actions.open_browser()
    
    try:
        logger.info("🚀 Starting session: %s", file_path)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Cannot find flow file: {file_path}")
            
        with open(file_path, "r") as f:
            lines = f.readlines()
            
        for line_num, line in enumerate(lines, 1):
            step = line.strip()
            
            # Skip empty lines and comments
            if not step or step.startswith("#"): 
                continue
                
            try:
                interpret(step)
                stats["passed"] += 1
                stats["log"].append(f"Line {line_num}: ✅ {step}")
            except Exception as e:
                stats["failed"] += 1
                
                # Removed the [:60] truncation to allow full error visibility in the summary
                error_msg = str(e).strip()
                stats["log"].append(f"Line {line_num}: ❌ {step} -> {error_msg}")
                logger.error("❌ Failure at Line %s: %s", line_num, error_msg)
                
                if STOP_ON_FAILURE:
                    logger.critical("🛑 STOP_ON_FAILURE is enabled. Halting test suite.")
                    break
                else:
                    logger.info("ℹ️ Continuing after failure.")
                    continue
                    
    except Exception as e:
        logger.error("❌ Critical Engine Error: %s", e)
    finally:
        test_label = os.path.basename(file_path).split('.')[0]
        
    # Teardown
    try:
        if PAGE:
            actions.close_browser(PAGE, test_label)
    except Exception as e:
        logger.error("Browser close failed: %s", e)
        
    # Generate Summary Report
    print("\n" + "="*80)
    print(f"📊 TEST SUMMARY: {test_label.upper()}")
    print("="*80)
    for entry in stats["log"]: 
        print(entry)
    print("="*80)
    print(f"TOTAL: {stats['passed'] + stats['failed']} | PASSED: {stats['passed']} | FAILED: {stats['failed']}")
    print("="*80 + "\n")
    
    # Sync Snippets for VS Code Auto-complete
    try:
        sync_locators_to_snippets()
        logger.info("🔄 VS Code Snippets synchronized.")
    except Exception as e:
        logger.warning("⚠️ Snippet sync failed: %s", e)

if __name__ == "__main__":
    flow_file = sys.argv[1] if len(sys.argv) > 1 else "steps.flow"
    run_steps(flow_file)