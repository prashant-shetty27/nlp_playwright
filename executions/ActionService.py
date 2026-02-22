import logging
from os import name
from .execution_session import ExecutionSession
from config_properties.site_registry import SITES

class ActionService:

    def __init__(self, session: ExecutionSession):
        self.session = session
        self.page = session.page
        self.logger = session.logger

    # ---------------------------------
    # INTERNAL SAFE EXECUTOR
    # ---------------------------------
    def _safe(self, action_name: str, func):
        """Runs any action safely with popup handling."""
        try:
            self.logger.info("▶ Action start: %s", action_name)

            self.session.handle_popups()
            result = func()
            self.session.handle_popups()

            self.logger.info("✔ Action success: %s", action_name)
            return result

        except Exception:
            self.logger.exception("✖ Action failed: %s", action_name)
            raise

    # ---------------------------------
    # OPEN SITE
    # ---------------------------------
    def open_site(self, name: str):
        name = name.lower().strip()

        if name not in SITES:
            raise ValueError(f"Unsupported site: {name}")
        url = SITES[name]

        def action():
            self.page.goto(url, wait_until="load", timeout=30000)
            self.logger.info(f"🌐 Page Loaded: {name.capitalize()}", action)
            
        self._safe("open_site: justdial", action)

    # ---------------------------------
    # WAIT
    # ---------------------------------
def wait(self, seconds: float):
    try:
        seconds = float(seconds)
        if seconds < 0:
            raise ValueError("Wait time must be non-negative")
    except Exception:
        raise ValueError("Wait command requires a numeric value")

    def action():
        self.page.wait_for_timeout(seconds * 1000)

    self._safe(f"wait {seconds:.1f}s", action)

