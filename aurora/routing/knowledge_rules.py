"""
Routing rules for questions, summaries and comparisons over the user's documents.

    what do my notes say about dark matter?
    according to my notes, how much of the universe is dark matter?
    in thesis.pdf, how many planets are in the sample?
    ask my documents what the event horizon is
    summarize thesis.pdf · summarize my thesis · summarize what my notes say about black holes
    compare notes.md and lecture.txt on dark matter
    compare what my documents and Wikipedia say about dark matter
    what's in my knowledge base? · re-index my documents
"""

import re

from aurora.routing import decision as targets
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent

_DOCS = r"(?:documents?|docs|notes|files|papers|pdfs?|knowledge\s+base)"
_FILE = r"[\w .()'-]+?\.(?:pdf|txt|md)"
# "my documents" / "my notes" (all), "thesis.pdf", or "my thesis" (a document by name).
# "my <word>" counts as a document name only in phrasings that can't be about
# anything else ("according to my thesis", "what does my thesis say about"):
# "what's the temperature in my room" is about the room, not a document.
_WHERE = rf"(?:my\s+{_DOCS}|(?:my\s+|the\s+)?(?P<file>{_FILE})|my\s+(?P<named>[a-z][\w-]{{2,30}}))"
_WHERE_STRICT = rf"(?:my\s+{_DOCS}|(?:my\s+|the\s+)?(?P<file>{_FILE}))"
_QUESTION = r"(?:what|who|whom|when|where|why|how|which|is|are|does|do|did|can|was|were)\b"

_TRAIL = re.compile(r"[\s?.!]+$")


def _clean(value: str) -> str:
    return _TRAIL.sub("", value or "").strip()


def _decision(arguments: dict, reason: str, confidence: float = 0.96) -> RouteDecision:
    return RouteDecision(Intent.KNOWLEDGE_BASE, confidence, targets.KNOWLEDGE, arguments, reason)


def _document(match) -> str:
    groups = match.groupdict()
    return (groups.get("file") or groups.get("named") or "").strip()


_ASK = [
    re.compile(rf"^what\s+(?:do|does)\s+{_WHERE}\s+say\s+about\s+(?P<q>.+)$", re.I),
    re.compile(rf"^according\s+to\s+{_WHERE}\s*,?\s*(?P<q>.+)$", re.I),
    re.compile(rf"^(?:in|from)\s+{_WHERE_STRICT}\s*,\s*(?P<q>{_QUESTION}.+)$", re.I),
    re.compile(rf"^ask\s+{_WHERE}\s+(?:about\s+)?(?P<q>.+)$", re.I),
    re.compile(rf"^(?P<q>{_QUESTION}.+?)\s+according\s+to\s+{_WHERE}$", re.I),
    re.compile(rf"^(?P<q>{_QUESTION}.+?)\s+in\s+{_WHERE_STRICT}$", re.I),
]
_SUMMARY_TOPIC = [
    re.compile(rf"^(?:summari[sz]e|sum\s+up)\s+what\s+my\s+{_DOCS}\s+says?\s+about\s+(?P<topic>.+)$", re.I),
    re.compile(rf"^(?:summari[sz]e|sum\s+up)\s+my\s+{_DOCS}\s+(?:on|about)\s+(?P<topic>.+)$", re.I),
]
_SUMMARY_DOC = re.compile(
    rf"^(?:summari[sz]e|sum\s+up|give\s+me\s+a\s+summary\s+of|what(?:'s|\s+is)\s+in)\s+"
    rf"(?:(?:my\s+|the\s+)?(?P<file>{_FILE})|my\s+(?P<named>(?!{_DOCS}\b)[a-z][\w-]{{2,30}}))$", re.I)
_COMPARE = [
    re.compile(r"^compare\s+what\s+(?P<a>.+?)\s+and\s+(?P<b>.+?)\s+say\s+(?:about|on)\s+(?P<topic>.+)$", re.I),
    re.compile(r"^compare\s+(?P<a>.+?)\s+(?:and|with|to|against)\s+(?P<b>.+?)\s+(?:on|about|regarding)\s+(?P<topic>.+)$", re.I),
]
_STATUS = re.compile(rf"^(?:what(?:'s|\s+is)\s+in\s+my\s+knowledge\s+base|knowledge\s+base(?:\s+status)?|"
                     rf"which\s+(?:documents|files)\s+(?:are|have\s+been)\s+indexed)$", re.I)
_REINDEX = re.compile(r"^(?:re-?index|rebuild|refresh)\s+(?:my\s+|the\s+)?(?:documents|knowledge\s+base|"
                      r"document\s+index|index)$", re.I)


def _side(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^(?:the\s+)", "", text, flags=re.I)
    if re.fullmatch(rf"my\s+{_DOCS}", text, re.I):
        return "documents"
    return re.sub(r"^my\s+", "", text, flags=re.I)


def knowledge_base_rule(original: str, body: str) -> RouteDecision | None:
    text = _clean(body)
    if _REINDEX.match(text):
        return _decision({"action": "reindex"}, "The user asks to rebuild the document index.")
    if _STATUS.match(text):
        return _decision({"action": "status"}, "The user asks what is in their knowledge base.")
    for pattern in _COMPARE:
        match = pattern.match(text)
        if match:
            return _decision({"action": "compare", "topic": _clean(match.group("topic")),
                              "sides": (_side(match.group("a")), _side(match.group("b")))},
                             "The user asks to compare what two sources say.")
    for pattern in _SUMMARY_TOPIC:
        match = pattern.match(text)
        if match:
            return _decision({"action": "summarize", "topic": _clean(match.group("topic"))},
                             "The user asks for a summary of a topic in their documents.")
    match = _SUMMARY_DOC.match(text)
    if match:
        return _decision({"action": "summarize", "document": _document(match)},
                         "The user asks for a summary of a document.")
    for pattern in _ASK:
        match = pattern.match(text)
        if match:
            arguments = {"action": "ask", "question": _clean(match.group("q"))}
            if _document(match):
                arguments["document"] = _document(match)
            return _decision(arguments, "The user asks a question about their documents.")
    return None
