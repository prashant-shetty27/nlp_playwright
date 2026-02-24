import time
import logging
from functools import wraps
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

# ==========================================
# ARCHITECTURAL MIDDLEWARE: ACTION RETRY
# ==========================================
def with_retry(max_attempts: int = 3, delay: float = 2.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            
            while attempts < max_attempts:
                try:
                    # Attempt to execute the core action
                    return func(*args, **kwargs)
                    
                except (PlaywrightError, PlaywrightTimeoutError) as e:
                    attempts += 1
                    
                    if attempts >= max_attempts:
                        logging.error(f"Action '{func.__name__}' failed completely after {max_attempts} attempts.")
                        # Re-raise the exception to trigger the final test failure in runner.py
                        raise RuntimeError(f"Action '{func.__name__}' failed: {e}")
                    
                    logging.warning(f"Attempt {attempts}/{max_attempts} for '{func.__name__}' failed. Retrying in {delay}s... (Trace: {e})")
                    time.sleep(delay)
                    
        return wrapper
    return decorator