"""
execution/retry.py
@with_retry decorator — architectural middleware for resilience against transient Playwright errors.
Moved from utils.py.
"""
import time
import logging
from functools import wraps
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError


def with_retry(max_attempts: int = 3, delay: float = 2.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            while attempts < max_attempts:
                try:
                    return func(*args, **kwargs)
                except (PlaywrightError, PlaywrightTimeoutError) as e:
                    attempts += 1
                    if attempts >= max_attempts:
                        logging.error(
                            f"Action '{func.__name__}' failed completely after {max_attempts} attempts."
                        )
                        raise RuntimeError(f"Action '{func.__name__}' failed: {e}")
                    logging.warning(
                        f"Attempt {attempts}/{max_attempts} for '{func.__name__}' failed. "
                        f"Retrying in {delay}s... (Trace: {e})"
                    )
                    time.sleep(delay)
        return wrapper
    return decorator
