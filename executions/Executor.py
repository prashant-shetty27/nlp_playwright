import logging


class Executor:
    """
    Central command dispatcher.

    Responsibilities:
    - Receives parsed DSL command objects
    - Routes command to ActionService
    - Handles logging + error boundaries
    """

    # -------------------------------------------------
    # INIT
    # -------------------------------------------------
    def __init__(self, action_service, logger: logging.Logger | None = None):
        self.actions = action_service
        self.logger = logger or logging.getLogger(__name__)

        # Command routing table (created once)
        self.dispatch_map = {
            "open": self._cmd_open,
            "wait": self._cmd_wait,
            "search": self._cmd_search,
            "verify_text": self._cmd_verify_text,
            "scroll_until_text": self._cmd_scroll_until_text,
        }

    # -------------------------------------------------
    # MAIN EXECUTION ENTRY
    # -------------------------------------------------
    def execute(self, command):
        """
        Execute parsed command.
        """
        command_type = command.type

        self.logger.info("▶ Executing command: %s", command_type)

        handler = self.dispatch_map.get(command_type)

        if not handler:
            raise ValueError(f"Unknown command: {command_type}")

        try:
            result = handler(command)
            self.logger.info("✔ Command completed: %s", command_type)
            return result

        except Exception:
            self.logger.exception("✖ Command failed: %s", command_type)
            raise

    # -------------------------------------------------
    # COMMAND HANDLERS
    # -------------------------------------------------

    def _cmd_open(self, command):
        if not command.target:
            raise ValueError("open command requires target")
        return self.actions.open_site(command.target)

    def _cmd_wait(self, command):
        if command.wait is None:
            raise ValueError("wait command requires seconds")
        return self.actions.wait(command.wait)

    def _cmd_search(self, command):
        if not command.text:
            raise ValueError("search command requires text")
        return self.actions.search(command.text)

    def _cmd_verify_text(self, command):
        return self.actions.verify_text(
            text=command.text,
            scroll_count=command.scroll_count
        )

    def _cmd_scroll_until_text(self, command):
        return self.actions.scroll_until_text(
            text=command.text,
            scroll_count=command.scroll_count,
            scroll_wait=command.scroll_wait,
        )
