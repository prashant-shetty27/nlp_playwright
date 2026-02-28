"""
locators/watcher.py
LocatorWatcher — file system observer for live locator hot-reloading.
Extracted from locator_manager.py.
"""
import json
import logging

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger(__name__)


class LocatorWatcher(FileSystemEventHandler):
    """
    Watches the locator JSON file for changes and hot-reloads into memory.
    Signals UI/frontend to refresh snippet dropdowns when the file is updated.
    """

    def __init__(self, json_file_path: str):
        self.file_path = json_file_path
        self.live_locators: dict = {}
        self.load_into_memory()

    def load_into_memory(self) -> None:
        try:
            with open(self.file_path, "r") as f:
                self.live_locators = json.load(f)
            logger.info("🔄 Locators reloaded from %s", self.file_path)
        except Exception as e:
            logger.error("❌ Error loading locators: %s", e)

    def on_modified(self, event) -> None:
        if event.src_path.endswith(self.file_path):
            self.load_into_memory()


def start_watcher(path: str) -> LocatorWatcher:
    """Starts a background file-system observer for the given locator JSON path."""
    watcher = LocatorWatcher(path)
    observer = Observer()
    watch_dir = path.rsplit("/", 1)[0] or "."
    observer.schedule(watcher, path=watch_dir, recursive=False)
    observer.start()
    return watcher
