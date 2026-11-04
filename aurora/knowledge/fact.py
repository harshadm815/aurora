from dataclasses import dataclass
from datetime import datetime


@dataclass
class Fact:
    category: str
    entity: str
    attribute: str
    value: str
    priority: int
    confidence: float = 1.0
    id: int | None = None
    created: datetime | None = None
    last_used: datetime | None = None
    times_used: int = 0