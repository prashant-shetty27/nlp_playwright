"""
registry.py
Backward-compatible shim — delegates to core.registry.
Import ACTION_REGISTRY and codeless_snippet from here for legacy compatibility.
"""
from core.registry import ACTION_REGISTRY, codeless_snippet, get_available_snippets

__all__ = ["ACTION_REGISTRY", "codeless_snippet", "get_available_snippets"]