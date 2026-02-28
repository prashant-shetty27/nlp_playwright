"""
execution/session.py
TestSession — encapsulates browser lifecycle state that was previously scattered as module-level globals.

Also contains ExecutionSession for the DI-based flow path (merged from executions/execution_session.py).
"""
import logging
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError


# ─────────────────────────────────────────────────────────────────────────────
# TEST SESSION: replaces scattered globals (_playwright_instance, _browser, _context, RUNTIME_VARIABLES)
# ─────────────────────────────────────────────────────────────────────────────
class TestSession:
    """
    Single container for all browser-level state. Passed to browser_manager functions.
    Replaces the module-level globals in the old actions.py.
    """
    def __init__(self):
        self.playwright_instance = None
        self.browser = None
        self.context = None
        self.page = None


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION SESSION: DI-based session for flow_runner path (from executions/)
# ─────────────────────────────────────────────────────────────────────────────
class ExecutionSession:
    """
    Session context wrapper for DI-based flow runner.
    Handles automatic Justdial popup dismissal.
    """
    def __init__(self, page, logger: logging.Logger):
        self.page = page
        self.logger = logger
        self.last_search_performed = False

    def handle_popups(self):
        """Clears known Justdial popups dynamically."""
        if not self.page:
            self.logger.warning("Popup handler skipped — page not available")
            return

        popups = [
            "span.modal-close",
            "text='Maybe Later'",
            ".close-popup",
            "section.blocks-login span",
            ".close__62",
        ]
        closed_count = 0

        for selector in popups:
            try:
                element = self.page.locator(selector).first
                if element.is_visible(timeout=500):
                    element.click(timeout=500)
                    self.logger.info("🧹 Popup swept: %s", selector)
                    closed_count += 1
            except PlaywrightTimeoutError:
                self.logger.debug("Popup not present: %s", selector)
            except PlaywrightError as e:
                self.logger.warning("Popup interaction failed [%s]: %s", selector, e)
            except Exception:
                self.logger.exception("Unexpected popup handler error [%s]", selector)

        self.logger.info("Popup sweep complete — %d closed", closed_count)
