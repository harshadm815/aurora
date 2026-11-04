from dataclasses import dataclass
from datetime import datetime


@dataclass
class Memory:

    id: int

    category: str

    content: str

    priority: int

    created: datetime

    last_used: datetime

    times_used: int = 0