"""
core/registry.py
Global action registry — decorator-based auto-registration for all codeless snippets.
Supports multi-platform action registration.
"""
from typing import Callable, Dict

ACTION_REGISTRY: Dict[str, Callable] = {}


def codeless_snippet(snippet_name: str):
    """
    Decorator that automatically registers a function into the ACTION_REGISTRY.
    Works across all platforms (web, mobile, android, ios, hybrid).

    Usage:
        @codeless_snippet("click element")
        def click_element(page, locator): ...
    """
    def decorator(func: Callable):
        ACTION_REGISTRY[snippet_name] = func
        return func
    return decorator


def get_available_snippets() -> list:
    """Returns all registered snippet names."""
    return list(ACTION_REGISTRY.keys())
