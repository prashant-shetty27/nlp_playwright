from dataclasses import dataclass
from typing import Optional, List, Union

@dataclass
class Command:
    type: str
    text: Optional[str] = None
    values: Optional[Union[str, List[str]]] = None
    count: Optional[int] = None
    wait: Optional[float] = None
    stop: Optional[bool] = None
    target: Optional[str] = None
