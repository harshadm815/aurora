from dataclasses import dataclass
from datetime import datetime

# How a memory came to exist (provenance).
AUTOMATIC = "automatic"     # learned from ordinary conversation
EXPLICIT = "explicit"       # the user asked: "remember that ..."
EDITED = "edited"           # the user changed it (Memory Centre or a correction)
UNKNOWN = "unknown"         # saved before AURORA kept track (memories from before 0.26)


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
    # Provenance and retention (AURORA 0.26).
    source: str = AUTOMATIC
    source_text: str | None = None      # the sentence it was learned from, if kept
    updated: datetime | None = None
    pinned: bool = False                 # never expires, kept first in context
    expires: datetime | None = None      # deleted automatically after this time
    times_confirmed: int = 1             # how often the user has said it
