"""
Where an answer came from.

AURORA must never blur these together, because that is how assistants
end up presenting guesses as facts:

    model     the language model's training data: unverified, may be wrong or out of date
    memory    something the user told AURORA (aurora_memory.db)
    document  a passage in one of the user's documents (file, page)
    live      a live external service, fetched just now (Wikipedia, arXiv, Open-Meteo)
    computed  calculated by AURORA's own code (calculator, statistics)
    sensor    a reading from a sensor (or the simulator)

Tools attach Sources to their results; the engine collects them for each
reply (engine.last_sources), and the Command Centre shows them under it.
"""

from dataclasses import dataclass, field
from datetime import datetime

MODEL, MEMORY, DOCUMENT, LIVE, COMPUTED, SENSOR = "model", "memory", "document", "live", "computed", "sensor"
KINDS = (MODEL, MEMORY, DOCUMENT, LIVE, COMPUTED, SENSOR)

LABELS = {
    MODEL: "language model (training data, not checked)",
    MEMORY: "AURORA's memory",
    DOCUMENT: "your documents",
    LIVE: "live source",
    COMPUTED: "calculated",
    SENSOR: "sensor reading",
}


@dataclass(frozen=True)
class Source:
    kind: str
    title: str = ""                  # "thesis.pdf", "Wikipedia: Black hole", "Open-Meteo"
    location: str = ""               # "page 3", or a URL
    retrieved: datetime | None = field(default=None, compare=False)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown source kind {self.kind!r}")

    def citation(self) -> str:
        """One line, e.g. 'thesis.pdf, page 3' or 'Wikipedia, "Black hole", https://…, retrieved 07 Nov 2026'."""
        parts = [self.title] if self.title else [LABELS[self.kind]]
        if self.location:
            parts.append(self.location)
        if self.retrieved is not None and self.kind == LIVE:
            parts.append(f"retrieved {self.retrieved:%d %b %Y %H:%M}")
        return ", ".join(parts)

    def short(self) -> str:
        """For a one-line caption: 'your documents: thesis.pdf p. 3'."""
        if self.kind == DOCUMENT and self.title:
            if self.location.startswith("page "):
                where = self.location.replace("page ", "p. ")
            else:                                   # e.g. "image, analysed on this computer"
                where = f"({self.location})" if self.location else ""
            return f"{LABELS[DOCUMENT]}: {self.title}" + (f" {where}" if where else "")
        if self.kind == LIVE and self.title:
            return f"live: {self.title}"
        return LABELS[self.kind]


def merge(*groups) -> list[Source]:
    """All sources from several lists, without repeats, in first-seen order."""
    seen, result = set(), []
    for group in groups:
        for source in group or ():
            key = (source.kind, source.title, source.location)
            if key not in seen:
                seen.add(key)
                result.append(source)
    return result


def caption(sources: list[Source], limit: int = 4) -> str:
    """'Sources: your documents: thesis.pdf p. 3 · live: Wikipedia …'"""
    if not sources:
        return ""
    parts = [s.short() for s in sources[:limit]]
    more = f" (+{len(sources) - limit} more)" if len(sources) > limit else ""
    return "Sources: " + " · ".join(dict.fromkeys(parts)) + more


def numbered(sources: list[Source]) -> str:
    """A 'Sources:' block for the end of an answer: [1] …, [2] …"""
    return "Sources:\n" + "\n".join(f"  [{n}] {s.citation()}" for n, s in enumerate(sources, start=1))
