import re
from dataclasses import dataclass, field

from aurora.knowledge.fact import Fact
from aurora.memory.store import MemoryStore

# Words that carry no meaning for finding a memory.
_STOPWORDS = {
    "a", "an", "the", "my", "me", "myself", "i", "i'm", "im", "you", "your",
    "is", "are", "was", "were", "be", "about", "of", "on", "in", "at", "to",
    "for", "and", "or", "what", "who", "where", "when", "which", "how",
    "do", "did", "does", "that", "this", "it", "told", "tell", "said", "say",
    "know", "remember", "anything", "something", "everything", "all",
}

# Spelling variants treated as the same word.
_VARIANTS = {
    "colour": "color", "colours": "colors",
    "favourite": "favorite", "favourites": "favorites",
}

_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return [_VARIANTS.get(w, w) for w in _WORD.findall(text.lower())]


def keywords(query: str) -> list[str]:
    """The meaningful words of a query, e.g. 'my favourite colour' -> ['favorite', 'color']."""
    return [w for w in _words(query) if w not in _STOPWORDS]


def _searchable(fact: Fact) -> set[str]:
    return set(_words(f"{fact.category} {fact.entity} {fact.attribute} {fact.value}"))


@dataclass
class RecallResult:
    """
    ``matches``: memories containing every keyword of the query.
    ``related``: memories containing only some of them.
    """

    keywords: list[str]
    matches: list[Fact] = field(default_factory=list)
    related: list[Fact] = field(default_factory=list)


class MemoryManager:
    """
    High-level interface for AURORA's long-term memory.
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
        Finds memories by keyword.

        A query with no meaningful words ("what do you know about me")
        matches every memory. Exact duplicates are shown once.
        """
        words = keywords(query)
        result = RecallResult(words)

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
            hits = sum(1 for w in words if w in found)
            if hits == len(words):
                seen.add(key)
                result.matches.append(fact)
            elif hits:
                seen.add(key)
                result.related.append(fact)

        result.matches = result.matches[:limit]
        result.related = result.related[:limit]
        return result

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
