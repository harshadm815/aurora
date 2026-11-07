"""
Fact extraction: turns first-person statements into candidate Facts.

    "My name is Harry and I live in Pune."
        -> Personal / User / Name = Harry
        -> Personal / User / Lives in = Pune

The extractor only *proposes* facts with a confidence. It never saves
anything; the MemoryEvaluator decides what is worth keeping.

It is deliberately rule-based and conservative: a small local model
cannot be trusted to produce reliable structured output, and a missed
fact is far cheaper than a wrong one. It ignores:

- questions ("is my name Harry?"),
- hypotheticals and uncertainty ("if my name were…", "maybe I like…"),
- negated statements ("my name is not…"),
- any message mentioning sensitive data (passwords, card or account
  numbers, government IDs, long digit sequences).
"""

import re

from aurora.knowledge.fact import Fact

# --- Message-level guards ---------------------------------------------------

_SENSITIVE = re.compile(
    r"\b(?:password|passcode|passphrase|pin|otp|cvv|card\s+number|credit\s+card|"
    r"debit\s+card|bank\s+account|account\s+number|iban|ssn|social\s+security|"
    r"aadhaa?r|pan\s+card|passport|api\s+key|secret\s+key|token)\b"
    r"|\d[\d\s-]{7,}\d",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?;])\s+|\n+|,\s*but\s+|\s+but\s+"
    r"|,?\s+and\s+(?=(?:i|i'm|im|my)\b)",        # "My name is Harry and I live in Pune"
    re.IGNORECASE,
)

_QUESTION_START = re.compile(
    r"^(?:is|are|am|was|were|do|does|did|can|could|would|will|should|"
    r"what|who|where|when|why|how|which)\b",
    re.IGNORECASE,
)

_UNCERTAIN = re.compile(
    r"\b(?:if|would|wish|maybe|perhaps|might|probably|not\s+sure|pretend|"
    r"imagine|suppose|supposed|joking|kidding)\b",
    re.IGNORECASE,
)

_FILLER = re.compile(
    r"^(?:(?:well|also|oh|btw|by\s+the\s+way|actually|and|so|hi|hello|hey|"
    r"aurora|just\s+so\s+you\s+know|fyi)[\s,!]+)+",
    re.IGNORECASE,
)

# Where a value stops: "I like pizza because it's tasty" -> "pizza".
_VALUE_END = re.compile(
    r"\s+(?:because|since|as|so|which|who|when|while|though|although|"
    r"but|and\s+i|and\s+my|and\s+i'm)\b.*$",
    re.IGNORECASE,
)

_TRAILING = re.compile(r"[\s.!?;:,\"')]+$")
_LEADING = re.compile(r"^[\s\"'(]+")


def _clean(value: str) -> str:
    value = _VALUE_END.sub("", value)
    value = _TRAILING.sub("", value)
    return _LEADING.sub("", value).strip()


def _split_list(value: str) -> list[str]:
    """'tea, coffee and chess' -> ['tea', 'coffee', 'chess']"""
    parts = re.split(r"\s*,\s*(?:and\s+|or\s+)?|\s+and\s+|\s+or\s+", value)
    return [p.strip() for p in parts if p.strip()]


def _title(value: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in value.split())


# --- Patterns ---------------------------------------------------------------
#
# Each pattern: (compiled regex, builder). The builder receives the
# match and returns a list of Facts.

_I = r"(?:i|I)"
_IM = r"(?:i\s+am|i'm|im)"
_ADVERB = r"(?:(?:really|also|absolutely|truly|totally|kind\s+of|kinda)\s+)?"


def _fact(category, attribute, value, confidence, entity="User") -> Fact:
    return Fact(
        category=category,
        entity=entity,
        attribute=attribute,
        value=value,
        priority=3,          # The evaluator assigns the real priority.
        confidence=confidence,
    )


def _single(category, attribute, confidence, transform=None):
    def build(match):
        value = _clean(match.group("value"))
        if transform:
            value = transform(value)
        return [_fact(category, attribute, value, confidence)] if value else []
    return build


def _multi(category, attribute, confidence):
    def build(match):
        value = _clean(match.group("value"))
        value = re.sub(r"^to\s+", "", value, flags=re.IGNORECASE)  # "I like to read" -> "read"
        return [_fact(category, attribute, v, confidence) for v in _split_list(value)]
    return build


def _favourite(match):
    thing = _title(match.group("thing").strip())
    values = _split_list(_clean(match.group("value")))
    return [_fact("Preference", f"Favourite {thing}", v, 0.9) for v in values[:1]]


_RELATIONS = {
    "best friend": "Best Friend", "sister": "Sister", "brother": "Brother",
    "mother": "Mother", "mom": "Mother", "mum": "Mother",
    "father": "Father", "dad": "Father",
    "wife": "Wife", "husband": "Husband", "partner": "Partner",
    "girlfriend": "Girlfriend", "boyfriend": "Boyfriend",
    "son": "Son", "daughter": "Daughter", "friend": "Friend",
    "teacher": "Teacher", "professor": "Professor", "cousin": "Cousin",
    "roommate": "Roommate", "manager": "Manager",
}


def _relation(match):
    attribute = _RELATIONS[match.group("rel").lower()]
    return [_fact("Relationship", attribute, match.group("value"), 0.85)]


_NAME_VALUE = r"(?P<value>[A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*){0,2})"
_PROPER_NAME = r"(?P<value>[A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+)?)"

PATTERNS = [
    # Name
    (re.compile(rf"^(?i:my\s+name\s+is|{_IM}\s+called|my\s+name's)\s+{_NAME_VALUE}\s*$"),
     _single("Personal", "Name", 0.95, _title)),

    # Favourites: "my favourite subject is astrophysics"
    (re.compile(
        r"^my\s+(?:favou?rite|fav)\s+(?P<thing>[a-z][a-z ]{1,30}?)\s+(?:is|are|would\s+be)\s+(?P<value>.+)$",
        re.IGNORECASE), _favourite),

    # Dislikes (before likes, so "I don't like" isn't read as "like")
    (re.compile(
        rf"^{_I}\s+{_ADVERB}(?:don'?t|do\s+not)\s+(?:like|enjoy|love)\s+(?P<value>.+)$"
        rf"|^{_I}\s+{_ADVERB}(?:hate|dislike|detest|can'?t\s+stand)\s+(?P<value2>.+)$",
        re.IGNORECASE),
     lambda m: _multi("Preference", "Dislikes", 0.8)(_Alias(m))),

    # Likes
    (re.compile(rf"^{_I}\s+{_ADVERB}(?:like|love|enjoy|adore)\s+(?P<value>.+)$", re.IGNORECASE),
     _multi("Preference", "Likes", 0.8)),

    # Where the user lives / comes from
    (re.compile(rf"^(?:{_I}\s+(?:live|stay)|{_IM}\s+(?:living|staying))\s+in\s+(?P<value>.+)$", re.IGNORECASE),
     _single("Personal", "Lives in", 0.85, _title)),
    (re.compile(rf"^(?:{_IM}|{_I}\s+come)\s+from\s+(?P<value>.+)$", re.IGNORECASE),
     _single("Personal", "From", 0.85, _title)),

    # Education
    (re.compile(
        rf"^(?:{_I}\s+(?:study|go)\s+(?:at|to)|{_IM}\s+studying\s+at)\s+"
        rf"(?P<value>.*\b(?:college|school|university|institute|academy|iit|nit)\b.*)$",
        re.IGNORECASE),
     _single("Education", "Institution", 0.85)),
    (re.compile(rf"^(?:{_I}\s+(?:study|major\s+in)|{_IM}\s+(?:studying|majoring\s+in))\s+(?!at\b|in\b)(?P<value>.+)$",
                re.IGNORECASE),
     _single("Education", "Studies", 0.8)),

    # Work
    (re.compile(rf"^{_I}\s+work\s+as\s+(?:an?\s+)?(?P<value>.+)$", re.IGNORECASE),
     _single("Work", "Occupation", 0.85)),
    (re.compile(rf"^{_I}\s+work\s+(?:at|for)\s+(?P<value>.+)$", re.IGNORECASE),
     _single("Work", "Employer", 0.85)),

    # Birthday
    (re.compile(r"^my\s+birthday\s+is\s+(?:on\s+)?(?P<value>.+)$", re.IGNORECASE),
     _single("Personal", "Birthday", 0.9)),

    # Relationships: the name must be capitalised ("my sister is sick" is not a name).
    (re.compile(
        r"^(?i:my\s+(?P<rel>" + "|".join(sorted(_RELATIONS, key=len, reverse=True)) + r"))"
        r"(?i:'s\s+name\s+is|\s+is\s+called|\s+is\s+named|\s+is|'s)\s+" + _PROPER_NAME + r"\s*[.!]?$"),
     _relation),
]


class _Alias:
    """Lets a builder read ``value`` from either alternative of a pattern."""

    def __init__(self, match):
        self._match = match

    def group(self, name):
        if name == "value":
            return self._match.group("value") or self._match.group("value2")
        return self._match.group(name)


# --- Extractor --------------------------------------------------------------

# --- Facts about other people (associative memory) -------------------------
#
# "my sister Priya lives in Delhi"  -> Sister = Priya, and Priya: Lives in = Delhi
# "Priya likes chess"               -> Priya: Likes = chess   (only if Priya is a
#                                       name the user already told AURORA about)

_REL_WORDS = "|".join(sorted(_RELATIONS, key=len, reverse=True))

_PERSON_FACTS = [
    (r"(?:lives|stays|is\s+living)\s+in\s+(?P<value>.+)", "Personal", "Lives in", 0.8, _title),
    (r"(?:is|comes)\s+from\s+(?P<value>.+)", "Personal", "From", 0.8, _title),
    (r"works\s+as\s+(?:an?\s+)?(?P<value>.+)", "Work", "Occupation", 0.8, None),
    (r"works\s+(?:at|for)\s+(?P<value>.+)", "Work", "Employer", 0.8, None),
    (r"(?:studies|is\s+studying)\s+(?!at\b|in\b)(?P<value>.+)", "Education", "Studies", 0.75, None),
    (r"(?:likes|loves|enjoys)\s+(?P<value>.+)", "Preference", "Likes", 0.75, "list"),
    (r"(?:hates|dislikes|doesn'?t\s+like)\s+(?P<value>.+)", "Preference", "Dislikes", 0.75, "list"),
]
_PERSON_BIRTHDAY = re.compile(r"^(?P<name>[A-Z][a-z'-]+)'s\s+birthday\s+is\s+(?:on\s+)?(?P<value>.+)$")
_INTRODUCED = re.compile(
    r"^(?i:my\s+(?P<rel>" + _REL_WORDS + r"))(?:,)?\s+" + r"(?P<name>[A-Z][a-z'-]+)(?:,)?\s+(?P<rest>.+)$")


def _person_facts(name: str, rest: str) -> list[Fact]:
    for pattern, category, attribute, confidence, transform in _PERSON_FACTS:
        match = re.match(rf"^{pattern}$", rest, re.IGNORECASE)
        if not match:
            continue
        value = _clean(match.group("value"))
        if not value:
            return []
        if transform == "list":
            return [_fact(category, attribute, v, confidence, entity=name) for v in _split_list(value)]
        value = transform(value) if transform else value
        return [_fact(category, attribute, value, confidence, entity=name)]
    return []


# --- Extractor --------------------------------------------------------------

class FactExtractor:
    """
    Converts natural language into candidate Facts. Does not save anything.

    Each fact's ``source_text`` is set to the sentence it came from, so
    the memory can later say how it was learned.
    """

    def extract(self, text: str, people=()) -> list[Fact]:
        """``people``: names already known (from relationship memories);
        only statements about them are read as facts about someone else."""

        if not text or _SENSITIVE.search(text):
            return []

        known = {p for p in people if p}
        facts: list[Fact] = []
        for sentence in _SENTENCE_SPLIT.split(text):
            for fact in self._from_sentence(sentence, known):
                fact.source_text = " ".join(sentence.split()).strip()
                facts.append(fact)
                if fact.category == "Relationship":
                    known.add(fact.value)
        return facts

    def _from_sentence(self, sentence: str, people=frozenset()) -> list[Fact]:
        sentence = _FILLER.sub("", sentence.strip()).strip()

        if not sentence or sentence.endswith("?"):
            return []
        if _QUESTION_START.match(sentence) or _UNCERTAIN.search(sentence):
            return []

        sentence = _TRAILING.sub("", sentence)

        for pattern, build in PATTERNS:
            match = pattern.match(sentence)
            if match:
                return [f for f in build(match) if f.value]

        # "my sister Priya lives in Delhi": the relationship and a fact about her.
        match = _INTRODUCED.match(sentence)
        if match:
            about = _person_facts(match.group("name"), match.group("rest"))
            if about:
                relation = _fact("Relationship", _RELATIONS[match.group("rel").lower()], match.group("name"), 0.85)
                return [relation, *about]

        # "Priya lives in Delhi", for a name the user already mentioned.
        birthday = _PERSON_BIRTHDAY.match(sentence)
        if birthday and birthday.group("name") in people:
            value = _clean(birthday.group("value"))
            return [_fact("Personal", "Birthday", value, 0.8, entity=birthday.group("name"))] if value else []
        first, _, rest = sentence.partition(" ")
        if first in people and rest:
            return _person_facts(first, rest)
        return []
