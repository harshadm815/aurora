"""
High-level interface for AURORA's long-term (semantic) memory.

Responsibilities:
  - storing facts with provenance (how, when, and the sentence they came from)
  - duplicate detection (case, articles and punctuation ignored)
  - conflict resolution (single-valued attributes, likes vs dislikes)
  - confidence tracking (repeating something makes AURORA surer)
  - importance scoring (what matters most when space is short)
  - retention (pinned memories, expiry, a block list of things not to learn)
  - retrieval by keyword, with synonyms and links between people

It does not interpret natural language; extraction lives in extractor.py.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from aurora.config import settings
from aurora.knowledge.fact import AUTOMATIC, EDITED, EXPLICIT, Fact
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

# Words people use for what a memory calls something else. Each query word
# also matches the words listed for it ("hometown" finds "From: Nagpur").
SYNONYMS = {
    "hometown": ["from"], "home": ["live", "from"], "address": ["live"],
    "job": ["occupation", "employer", "work"], "profession": ["occupation"], "career": ["occupation"],
    "company": ["employer"], "office": ["employer"],
    "college": ["institution"], "university": ["institution"], "uni": ["institution"],
    "school": ["institution"], "course": ["studies", "study"], "major": ["studies", "study"],
    "subject": ["studies", "study", "subject"],
    "bday": ["birthday"], "birthdate": ["birthday"], "born": ["birthday", "from"],
    "mum": ["mother"], "mom": ["mother"], "dad": ["father"],
}

_WORD = re.compile(r"[a-z0-9']+")
_ARTICLE = re.compile(r"^(?:a|an|the|some|my)\s+", re.I)
_EDGE_PUNCTUATION = re.compile(r"^[\s\"'“”‘’.,!?;:()]+|[\s\"'“”‘’.,!?;:()]+$")

# Attributes that can only have one current value. A new value replaces
# the old one instead of being stored next to it.
SINGLE_VALUED = {
    "name", "birthday", "lives in", "from", "studies", "institution",
    "occupation", "employer", "mother", "father", "wife", "husband",
    "partner", "girlfriend", "boyfriend", "best friend",
}

# Attributes that contradict each other for the same value.
OPPOSITES = {"likes": "dislikes", "dislikes": "likes"}

RELATIONSHIP = "Relationship"

# Settings stored in the memory database (the user's switches).
LEARNING_PAUSED = "learning_paused"


def is_single_valued(attribute: str) -> bool:
    attribute = attribute.lower()
    return attribute in SINGLE_VALUED or attribute.startswith("favourite ")


def normalize_value(value: str) -> str:
    """The form used to compare values: 'The Tea.' and 'tea' are the same."""
    value = _EDGE_PUNCTUATION.sub("", value or "")
    value = _ARTICLE.sub("", value)
    return " ".join(value.casefold().split())


def _stem(word: str) -> str:
    """Very small stemmer: studies/studying/studied -> study, lives -> live."""
    for suffix, replacement in (("ies", "y"), ("ied", "y"), ("ing", ""), ("ed", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + replacement
    return word


def _words(text: str) -> list[str]:
    return [_stem(_VARIANTS.get(w, w)) for w in _WORD.findall(text.lower())]


def keywords(query: str) -> list[str]:
    """The meaningful words of a query, e.g. 'my favourite colour' -> ['favorite', 'color'].

    Filler words are removed *before* stemming, so "does" isn't stemmed to
    "doe" and kept as if it were meaningful."""
    raw = [w for w in _WORD.findall((query or "").lower()) if w not in _STOPWORDS]
    stemmed = [_stem(_VARIANTS.get(w, w)) for w in raw]
    return [w for w in stemmed if w not in _STOPWORDS]


def _alternatives(word: str) -> list[str]:
    raw = [word] + SYNONYMS.get(word, [])
    for key, values in SYNONYMS.items():
        if _stem(key) == word:
            raw += values
    return list(dict.fromkeys(_stem(w) for w in raw))


def _searchable(fact: Fact) -> set[str]:
    return set(_words(f"{fact.category} {fact.entity} {fact.attribute} {fact.value}"))


def _matches(word: str, found: set[str]) -> bool:
    for candidate in _alternatives(word):
        if candidate in found:
            return True
        # "living" -> "liv" still finds "live"; require 4+ letters to avoid noise.
        if any((len(candidate) >= 4 and other.startswith(candidate))
               or (len(other) >= 4 and candidate.startswith(other)) for other in found):
            return True
    return False


def importance(fact: Fact) -> float:
    """0-1. Pinned memories are 1. Otherwise mostly the evaluator's priority,
    plus how sure AURORA is and how often the memory has been useful."""
    if fact.pinned:
        return 1.0
    usage = min(fact.times_used, 10) / 10
    repeated = min(fact.times_confirmed - 1, 4) / 4
    return round(0.6 * fact.priority / 5 + 0.2 * fact.confidence + 0.1 * usage + 0.1 * repeated, 3)


def reinforced(confidence: float) -> float:
    """Confidence after the user repeats a fact: halfway to certain."""
    return round(min(1.0, confidence + (1.0 - confidence) / 2), 3)


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
    - ``"duplicate"``: already known; its confidence went up.
    - ``"blocked"``: the user said not to remember this; nothing stored.
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
        self.cleanup()

    # ------------------------------------------------------------ storing

    def find_duplicate(self, fact: Fact) -> Fact | None:
        key = normalize_value(fact.value)
        for existing in self.store.find_by_attribute(fact.entity, fact.attribute):
            if existing.category.lower() == fact.category.lower() and normalize_value(existing.value) == key:
                return existing
        return None

    def contains(self, fact: Fact) -> bool:
        return self.find_duplicate(fact) is not None

    def is_blocked(self, fact: Fact) -> bool:
        return self.store.is_blocked(fact.entity, fact.attribute, normalize_value(fact.value))

    def remember(self, fact: Fact) -> int:
        """Stores a fact unless the same fact already exists; returns its id."""
        existing = self.find_duplicate(fact)
        if existing is not None:
            return existing.id
        return self.store.add(fact)

    def learn(self, fact: Fact, source: str = AUTOMATIC, source_text: str | None = None) -> LearnResult:
        """
        Stores a fact, resolving conflicts with what is already known:

        - On the block list: nothing is stored.
        - Same fact (ignoring case, articles, punctuation): it is counted
          and its confidence rises; told explicitly, it becomes certain.
        - Single-valued attribute (name, birthday, favourite …) with a
          different value: the old value is replaced (the change is kept
          in the history), and any extra copies are removed.
        - "Likes X" vs "Dislikes X": the opposite is replaced.
        - Otherwise: added alongside existing memories.
        """
        fact.source = source
        fact.source_text = source_text if settings.MEMORY_KEEP_SOURCE_TEXT else None
        if source == EXPLICIT:
            fact.confidence = 1.0

        if self.is_blocked(fact):
            logger.info("Not remembering a blocked %s fact", fact.attribute)
            return LearnResult("blocked", fact)

        existing = self.find_duplicate(fact)
        if existing is not None:
            confidence = 1.0 if source == EXPLICIT else reinforced(existing.confidence)
            upgrade = EXPLICIT if source == EXPLICIT and existing.source != EXPLICIT else None
            self.store.confirm(existing.id, confidence, upgrade, fact.source_text if upgrade else None)
            refreshed = self.store.get(existing.id)
            return LearnResult("duplicate", refreshed or existing)

        if is_single_valued(fact.attribute):
            current = self.store.find_by_attribute(fact.entity, fact.attribute)
            if current:
                keep, extras = current[0], current[1:]
                self.store.update_value(keep.id, fact.value, reason="replaced by a newer statement",
                                        source=source, source_text=fact.source_text,
                                        confidence=fact.confidence)
                for extra in extras:
                    self.store.delete(extra.id)
                fact.id = keep.id
                logger.info("Memory updated: %s (id %s)", fact.attribute, keep.id)
                return LearnResult("updated", fact, keep)

        opposite = OPPOSITES.get(fact.attribute.lower())
        if opposite:
            for other in self.store.find_by_attribute(fact.entity, opposite):
                if normalize_value(other.value) == normalize_value(fact.value):
                    self.store.delete(other.id)
                    fact.id = self.store.add(fact)
                    logger.info("Memory updated: %s -> %s (id %s)", other.attribute, fact.attribute, fact.id)
                    return LearnResult("updated", fact, other)

        fact.id = self.store.add(fact)
        logger.info("Memory added: %s (id %s)", fact.attribute, fact.id)
        return LearnResult("added", fact)

    # ------------------------------------------------------------- reading

    def get(self, memory_id: int) -> Fact | None:
        return self.store.get(memory_id)

    def memories(self) -> list[Fact]:
        """All memories, most important first."""
        facts = self.store.all()
        return sorted(facts, key=lambda f: (-int(f.pinned), -f.priority, f.id))

    def search(self, query: str) -> list[Fact]:
        """Searches long-term memory for a literal substring."""
        return self.store.search(query)

    def people(self) -> dict[str, list[str]]:
        """Names the user has mentioned, with how they're related: {'Priya': ['Sister']}."""
        found: dict[str, list[str]] = {}
        for fact in self.store.all():
            if fact.category == RELATIONSHIP and fact.entity == "User":
                found.setdefault(fact.value, []).append(fact.attribute)
        return found

    def recall(self, query: str, limit: int = 10) -> RecallResult:
        """
        Finds memories by keyword (with light stemming and synonyms, so
        "where do I live" finds "Lives in: Pune" and "my hometown" finds
        "From: Nagpur").

        Links between people are followed: "where does my sister live"
        finds Sister: Priya, then Priya's "Lives in: Delhi".

        A query with no meaningful words ("what do you know about me")
        matches every memory. Duplicates are shown once.
        """
        words = keywords(query)
        result = RecallResult(words)
        everything = self.memories()

        # People named by a relationship word in the query ("sister" -> Priya).
        linked: dict[str, set[str]] = {}
        for fact in everything:
            if fact.category == RELATIONSHIP and fact.entity == "User":
                relation_words = set(_words(fact.attribute))
                used = {w for w in words if _matches(w, relation_words)}
                if used:
                    linked.setdefault(fact.value.lower(), set()).update(used)

        scored_related = []
        seen = set()
        for fact in everything:
            key = (fact.entity.lower(), fact.attribute.lower(), normalize_value(fact.value))
            if key in seen:
                continue
            if not words:
                seen.add(key)
                result.matches.append(fact)
                continue

            found = _searchable(fact)
            needed = words
            if fact.entity.lower() in linked:
                # "sister" is satisfied by the link to this person.
                needed = [w for w in words if w not in linked[fact.entity.lower()]] or words
            hits = sum(1 for w in needed if _matches(w, found))
            if hits == len(needed):
                seen.add(key)
                result.matches.append(fact)
            elif hits:
                seen.add(key)
                scored_related.append((hits, fact))

        scored_related.sort(key=lambda pair: (-pair[0], -importance(pair[1])))
        result.matches = result.matches[:limit]
        result.related = [fact for _, fact in scored_related[:limit]]
        return result

    def provenance(self, fact: Fact) -> str:
        """How AURORA knows this, in words, with its change history."""
        when = fact.created.strftime("%d %b %Y") if fact.created else "an unknown date"
        said = f": “{fact.source_text}”" if fact.source_text else ""
        if fact.source == EXPLICIT:
            line = f"You asked me to remember it on {when}{said}."
        elif fact.source == AUTOMATIC:
            line = f"I learned it from something you said on {when}{said}."
        elif fact.source == EDITED:
            line = f"You set it yourself (last changed {fact.updated.strftime('%d %b %Y') if fact.updated else when})."
        else:
            line = f"It was saved on {when}, before I kept track of where memories came from."
        if fact.times_confirmed > 1:
            line += f" You've told me {fact.times_confirmed} times."
        for change in self.store.history(fact.id) if fact.id is not None else []:
            line += (f"\n    Changed on {change['changed']:%d %b %Y}: {change['old']} → {change['new']} "
                     f"({change['reason']}).")
        if fact.pinned:
            line += " It's pinned, so I'll always keep it."
        elif fact.expires:
            line += f" I'll forget it after {fact.expires:%d %b %Y %H:%M}."
        return line

    def mark_used(self, facts: list[Fact]) -> None:
        self.store.mark_used([fact.id for fact in facts if fact.id is not None])

    # ------------------------------------------------------------ changing

    def edit(self, memory_id: int, new_value: str) -> bool:
        """The user corrected a memory: it becomes certain, marked as edited."""
        new_value = (new_value or "").strip()
        if not new_value:
            return False
        return self.store.update_value(memory_id, new_value, reason="edited by you", source=EDITED,
                                       source_text=None, confidence=1.0)

    def pin(self, memory_id: int, pinned: bool = True) -> bool:
        return self.store.set_pinned(memory_id, pinned)

    def set_expiry(self, memory_id: int, expires: datetime | None) -> bool:
        return self.store.set_expires(memory_id, expires)

    def forget(self, memory_id: int) -> bool:
        """Deletes a memory (and its history)."""
        return self.store.delete(memory_id)

    def block(self, fact: Fact) -> None:
        """Never learn this again (the user said not to remember it)."""
        self.store.block(fact.entity, fact.attribute, fact.value, normalize_value(fact.value))

    def blocked(self) -> list[dict]:
        return self.store.blocked()

    def unblock(self, block_id: int) -> bool:
        return self.store.unblock(block_id)

    def cleanup(self, now: datetime | None = None) -> int:
        """Deletes memories whose time is up. Returns how many."""
        removed = self.store.delete_expired(now)
        if removed:
            logger.info("Expired memories removed: %d", removed)
        return removed

    # ---------------------------------------------------------- switches

    @property
    def learning_paused(self) -> bool:
        return self.store.get_setting(LEARNING_PAUSED, "0") == "1"

    def set_learning_paused(self, paused: bool) -> None:
        self.store.set_setting(LEARNING_PAUSED, "1" if paused else "0")
