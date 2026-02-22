import logging
from playwright.sync_api import Playwright, sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from locator_manager import load_locators
from clean_locators import sanitize_database
from sync_snippets import sync_locators_to_snippets
from command_model import Command
from command_parser import parse_step   

class ExecutionSession:
    def __init__(self, page, logger):
        self.page = page
        self.logger = logger
        self.last_search_performed = False

    # -----------------------------
    # POPUP HANDLER (belongs here)
    # -----------------------------
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
