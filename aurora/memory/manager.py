import logging
import re
from dataclasses import dataclass, field

from aurora.knowledge.fact import Fact
from aurora.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Words that carry no meaning for finding a memory.
_STOPWORDS = {
    "a", "an", "the", "my", "me", "myself", "i", "i'm", "im", "you", "your",
    "am", "is", "are", "was", "were", "be", "have", "has", "had", "about", "of", "on", "in", "at", "to",
    "for", "and", "or", "what", "who", "where", "when", "which", "how",
    "do", "did", "does", "that", "this", "it", "told", "tell", "said", "say",
    "know", "remember", "anything", "something", "everything", "all",
    "can", "could", "would", "should", "will", "please", "with", "from",
}

# Spelling variants treated as the same word.
_VARIANTS = {
    "colour": "color", "colours": "colors",
    "favourite": "favorite", "favourites": "favorites",
}

_WORD = re.compile(r"[a-z0-9']+")

# Attributes that can only have one current value. A new value replaces
# the old one instead of being stored next to it.
SINGLE_VALUED = {
    "name", "birthday", "lives in", "from", "studies", "institution",
    "occupation", "employer", "mother", "father", "wife", "husband",
    "partner", "girlfriend", "boyfriend", "best friend",
}

# Attributes that contradict each other for the same value.
OPPOSITES = {"likes": "dislikes", "dislikes": "likes"}


def is_single_valued(attribute: str) -> bool:
    attribute = attribute.lower()
    return attribute in SINGLE_VALUED or attribute.startswith("favourite ")


def _stem(word: str) -> str:
    """Very small stemmer: studies/studying/studied -> study, lives -> live."""
    for suffix, replacement in (("ies", "y"), ("ied", "y"), ("ing", ""), ("ed", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + replacement
    return word


def _words(text: str) -> list[str]:
    return [_stem(_VARIANTS.get(w, w)) for w in _WORD.findall(text.lower())]


def keywords(query: str) -> list[str]:
    """The meaningful words of a query, e.g. 'my favourite colour' -> ['favorite', 'color']."""
    return [
        w for w in _words(query)
        if w not in _STOPWORDS and _VARIANTS.get(w, w) not in _STOPWORDS
    ]


def _searchable(fact: Fact) -> set[str]:
    return set(_words(f"{fact.category} {fact.entity} {fact.attribute} {fact.value}"))


def _matches(word: str, found: set[str]) -> bool:
    if word in found:
        return True
    # "living" -> "liv" still finds "live"; require 4+ letters to avoid noise.
    return any(
        (len(word) >= 4 and other.startswith(word)) or (len(other) >= 4 and word.startswith(other))
        for other in found
    )


@dataclass
class RecallResult:
    """
    ``matches``: memories containing every keyword of the query.
    ``related``: memories containing only some of them.
    """

    keywords: list[str]
    matches: list[Fact] = field(default_factory=list)
    related: list[Fact] = field(default_factory=list)


@dataclass
class LearnResult:
    """
    What ``learn()`` did with a fact.

    ``action`` is one of:
    - ``"added"``: stored as a new memory.
    - ``"updated"``: replaced a conflicting memory (``previous`` holds the old one).
    - ``"duplicate"``: already known; nothing changed.
    """

    action: str
    fact: Fact
    previous: Fact | None = None


class MemoryManager:
    """
    High-level interface for AURORA's long-term memory.

    Stores, edits, deletes and retrieves memories, and resolves
    conflicts between them. It does not interpret natural language.
    """

    def __init__(self, store: MemoryStore | None = None):
        self.store = store or MemoryStore()

    def contains(self, fact: Fact) -> bool:
        return self.store.find_exact(fact) is not None

    def remember(self, fact: Fact) -> int:
        """
        Stores a fact unless an identical fact already exists.
        """

        existing = self.store.find_exact(fact)

        if existing is not None:
            return existing.id

        return self.store.add(fact)

    def learn(self, fact: Fact) -> LearnResult:
        """
        Stores a fact, resolving conflicts with what is already known:

        - Identical fact: nothing changes.
        - Single-valued attribute (name, birthday, favourite …) with a
          different value: the old value is replaced, and any extra
          copies are removed.
        - "Likes X" vs "Dislikes X": the opposite is replaced.
        - Otherwise: added alongside existing memories.
        """
        existing = self.store.find_exact(fact)
        if existing is not None:
            return LearnResult("duplicate", existing)

        if is_single_valued(fact.attribute):
            current = self.store.find_by_attribute(fact.entity, fact.attribute)
            if current:
                keep, extras = current[0], current[1:]
                self.store.update_value(keep.id, fact.value)
                for extra in extras:
                    self.store.delete(extra.id)
                fact.id = keep.id
                logger.info("Memory updated: %s (id %s)", fact.attribute, keep.id)
                return LearnResult("updated", fact, keep)

        opposite = OPPOSITES.get(fact.attribute.lower())
        if opposite:
            for other in self.store.find_by_attribute(fact.entity, opposite):
                if other.value.lower() == fact.value.lower():
                    self.store.delete(other.id)
                    fact.id = self.store.add(fact)
                    logger.info("Memory updated: %s -> %s (id %s)", other.attribute, fact.attribute, fact.id)
                    return LearnResult("updated", fact, other)

        fact.id = self.store.add(fact)
        logger.info("Memory added: %s (id %s)", fact.attribute, fact.id)
        return LearnResult("added", fact)

    def memories(self) -> list[Fact]:
        """
        Returns all memories.
        """
        return self.store.all()

    def search(self, query: str) -> list[Fact]:
        """
        Searches long-term memory for a literal substring.
        """
        return self.store.search(query)

    def recall(self, query: str, limit: int = 10) -> RecallResult:
        """
        Finds memories by keyword (with light stemming, so "where do I
        live" finds "Lives in: Pune").

        A query with no meaningful words ("what do you know about me")
        matches every memory. Exact duplicates are shown once.
        """
        words = keywords(query)
        result = RecallResult(words)
        scored_related = []

        seen = set()
        for fact in self.memories():
            key = (fact.attribute.lower(), fact.value.lower())
            if key in seen:
                continue

            if not words:
                seen.add(key)
                result.matches.append(fact)
                continue

            found = _searchable(fact)
            hits = sum(1 for w in words if _matches(w, found))
            if hits == len(words):
                seen.add(key)
                result.matches.append(fact)
            elif hits:
                seen.add(key)
                scored_related.append((hits, fact))

        scored_related.sort(key=lambda pair: (-pair[0], -pair[1].priority))
        result.matches = result.matches[:limit]
        result.related = [fact for _, fact in scored_related[:limit]]
        return result

    def mark_used(self, facts: list[Fact]) -> None:
        self.store.mark_used([fact.id for fact in facts if fact.id is not None])

    def edit(
        self,
        memory_id: int,
        new_value: str
    ) -> bool:
        """
        Edits an existing memory.
        """
        return self.store.update_value(
            memory_id,
            new_value
        )

    def forget(self, memory_id: int) -> bool:
        """
        Deletes an existing memory.
        """
        return self.store.delete(memory_id)
