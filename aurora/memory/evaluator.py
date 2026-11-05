"""
Memory evaluation: decides whether a candidate Fact is worth keeping,
and how important it is.

Does not save anything and does not interpret language.
"""

import re
from dataclasses import dataclass

from aurora.config import settings
from aurora.knowledge.fact import Fact

# Values that point at something instead of naming it.
_VAGUE = {
    "it", "that", "this", "these", "those", "them", "him", "her", "you",
    "something", "anything", "everything", "nothing", "things", "stuff",
    "there", "here", "none", "yes", "sure", "ok", "okay",
}

_NEGATED = re.compile(r"^(?:not|never|no)\b", re.IGNORECASE)

MAX_VALUE_WORDS = 6
MAX_VALUE_CHARS = 60

# Priority levels from Docs/MEMORY_DESIGN.md (5 = critical … 1 = temporary).
_PRIORITY = {
    "Name": 5,
    "Lives in": 4, "From": 4, "Birthday": 4,
    "Studies": 4, "Institution": 4, "Occupation": 4, "Employer": 4,
    "Likes": 3, "Dislikes": 3,
}
_RELATIONSHIP_PRIORITY = 4
_FAVOURITE_PRIORITY = 2
_DEFAULT_PRIORITY = 3


@dataclass
class Evaluation:
    keep: bool
    reason: str


class MemoryEvaluator:

    def __init__(self, min_confidence: float | None = None):
        self.min_confidence = (
            settings.MEMORY_MIN_CONFIDENCE if min_confidence is None else min_confidence
        )

    def priority(self, fact: Fact) -> int:
        if fact.attribute in _PRIORITY:
            return _PRIORITY[fact.attribute]
        if fact.category == "Relationship":
            return _RELATIONSHIP_PRIORITY
        if fact.attribute.startswith("Favourite "):
            return _FAVOURITE_PRIORITY
        return _DEFAULT_PRIORITY

    def evaluate(self, fact: Fact) -> Evaluation:
        value = fact.value.strip()
        words = value.split()

        if not value:
            return Evaluation(False, "empty value")
        if fact.confidence < self.min_confidence:
            return Evaluation(False, f"confidence {fact.confidence:.2f} below threshold")
        if value.lower() in _VAGUE or (words and words[0].lower() in _VAGUE):
            return Evaluation(False, "value is vague")
        if _NEGATED.match(value):
            return Evaluation(False, "value is negated")
        if len(words) > MAX_VALUE_WORDS or len(value) > MAX_VALUE_CHARS:
            return Evaluation(False, "value is too long to be a fact")
        if fact.attribute == "Name" and any(ch.isdigit() for ch in value):
            return Evaluation(False, "a name shouldn't contain digits")

        fact.priority = self.priority(fact)
        return Evaluation(True, "ok")
