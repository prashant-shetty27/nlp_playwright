from typing import Callable, Dict

# This dictionary serves as the single source of truth for your tool's snippets.
# It sits in memory and is automatically populated.
ACTION_REGISTRY: Dict[str, Callable] = {}

def codeless_snippet(snippet_name: str):
    def decorator(func: Callable):
        ACTION_REGISTRY[snippet_name] = func
        return func
    return decorator