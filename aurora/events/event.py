from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Event:
    """
    Something that happened inside AURORA.

    ``name`` is a dotted topic such as ``"kernel.started"`` (see
    ``aurora.events.topics``). ``source`` names the component that
    published it.
    """

    name: str
    data: Any = None
    source: str = "unknown"
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Event name must be a non-empty string.")
