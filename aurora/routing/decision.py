from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from aurora.routing.intent import Intent

# Component names a decision can target.
BRAIN = "Brain"
CALCULATOR = "Calculator"
WEATHER = "Weather"
MEMORY = "Memory"
WIKIPEDIA = "Wikipedia"
ARXIV = "arXiv"
DOCUMENTS = "Documents"
CAMERA = "Camera"
SENSORS = "Sensors"
HEALTH = "Health"
DEVICES = "Devices"
REMINDERS = "Reminders"


@dataclass(frozen=True, eq=True)
class RouteDecision:
    """
    The router's structured answer to "what should AURORA do with this?"

    - ``intent``: what the user wants.
    - ``confidence``: 0.0–1.0, how sure the router is.
    - ``target``: the component that should handle it (``None`` when
      clarification is needed).
    - ``arguments``: what the target needs, e.g. ``{"expression": "2+2"}``.
    - ``reason``: a human-readable explanation, for logs and debugging.
    - ``alternatives``: other candidates the router considered, best first.
    """

    intent: Intent
    confidence: float
    target: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""
    alternatives: tuple["RouteDecision", ...] = ()

    def __post_init__(self):
        if not isinstance(self.intent, Intent):
            raise TypeError("intent must be an Intent.")

        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0.")

        # Make arguments read-only so a decision can't be altered after routing.
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))
        object.__setattr__(self, "alternatives", tuple(self.alternatives))

    __hash__ = None  # Arguments are a mapping, so decisions aren't hashable.

    @property
    def needs_clarification(self) -> bool:
        return self.intent is Intent.CLARIFY

    @property
    def uses_tool(self) -> bool:
        return self.intent not in (Intent.CONVERSATION, Intent.CLARIFY)

    def to_dict(self) -> dict:
        return {
            "intent": self.intent.value,
            "confidence": round(self.confidence, 2),
            "target": self.target,
            "arguments": dict(self.arguments),
            "reason": self.reason,
            "alternatives": [
                {"intent": alt.intent.value, "confidence": round(alt.confidence, 2)}
                for alt in self.alternatives
            ],
        }
