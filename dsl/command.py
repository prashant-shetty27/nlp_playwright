import cmd
from dataclasses import dataclass
from typing import Optional


@dataclass
class Command:
    type: str
    target: Optional[str] = None
    wait: Optional[float] = None
    count: Optional[int] = None
    image_path: Optional[str] = None
    threshold: Optional[float] = None
    text: Optional[str] = None
