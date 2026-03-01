"""
core/command.py
Single unified Command dataclass for all platforms.
Merged from nlp/command.py, command_model.py, and dsl/command.py.
No hardcoded values — all fields are optional with safe defaults.
"""
from dataclasses import dataclass
from typing import Optional, List, Union


@dataclass
class Command:
    """
    Represents a single parsed automation step.
    Platform-agnostic — used by web, mobile, android, ios, hybrid adapters.
    """
    type: str
    text: Optional[str] = None
    target: Optional[str] = None
    values: Optional[Union[str, List[str]]] = None
    count: Optional[int] = None
    wait: Optional[float] = None
    stop: Optional[bool] = None
    # Image verification
    image_path: Optional[str] = None
    threshold: Optional[float] = None
    # Platform hint (web, mobile, android, ios, hybrid, device)
    platform: Optional[str] = None
    # Extract / store operations
    variable_name: Optional[str] = None   # target runtime variable name
    attribute: Optional[str] = None       # HTML attribute name (e.g. "href")
