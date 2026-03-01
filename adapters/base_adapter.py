"""
adapters/base_adapter.py
Abstract base class for all platform adapters.
Every adapter (web, mobile, android, ios, hybrid, device) must implement this interface.
"""
from abc import ABC, abstractmethod


class BaseAdapter(ABC):
    """
    Defines the unified interface for all platform adapters.
    Ensures consistent behavior across web, mobile, android, ios, hybrid, and device.
    """

    platform: str = "base"

    @abstractmethod
    def launch(self, **kwargs):
        """Launch a session (browser, device, emulator, etc.)."""
        ...

    @abstractmethod
    def quit(self, label: str = "session"):
        """Close/tear down the session."""
        ...

    @abstractmethod
    def navigate(self, url: str):
        """Navigate to a URL or app screen."""
        ...

    @abstractmethod
    def click(self, locator_name: str):
        """Click an element by locator name."""
        ...

    @abstractmethod
    def fill(self, locator_name: str, text: str):
        """Fill/type into an element."""
        ...
