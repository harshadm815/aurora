"""
Answering from documents, summarising and comparing, always with sources.

Answers are *extractive*: AURORA quotes the sentences in your documents
that best answer the question and cites where each one is. It never
writes an answer of its own here. The small local language model can't
be trusted to restate a source faithfully, and a quoted sentence can
always be checked.

    answer(question)          the best 1-3 sentences, numbered citations,
                              and a note if the documents disagree on a figure
    summarize_document(name)  the most representative sentences, in order, with pages
    summarize_topic(topic)    what the documents say about a topic
    compare(topic, a, b)      what two sources say, side by side, with
                              differing figures pointed out

If nothing in the documents answers the question, AURORA says so. It
never falls back to the language model's guess without saying that's
what it is.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from aurora.knowledge.index import Hit, KnowledgeIndex, query_terms, split_sentences
from aurora.knowledge.sources import DOCUMENT, Source, numbered
from aurora.memory.manager import _stem

FOUND = 0.6          # share of the question's (weighted) words a sentence must contain
PARTIAL = 0.34       # below FOUND but above this: "the closest I found"
MAX_SENTENCES = 3
MIN_WORDS = 4

_WORD = re.compile(r"[a-z0-9]+")
_FIGURE = re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(%|percent|per\s*cent|[a-z]+)?", re.I)
_UNIT_WORDS = {"%": "percent", "per cent": "percent"}


def stems(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower())}


@dataclass
class Quote:
    text: str
    document: str
    page: int | None
    score: float

    @property
    def source(self) -> Source:
        return Source(DOCUMENT, self.document, f"page {self.page}" if self.page else "")


@dataclass
class Answer:
    status: str                         # "found", "partial", "not_found", "no_documents", "empty_question"
    quotes: list[Quote] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def text(self, question: str = "") -> str:
        if self.status == "no_documents":
            return "There are no indexed documents yet. Put .txt, .md or .pdf files in your Documents folder."
        if self.status == "empty_question":
            return "What would you like to know from your documents?"
        if self.status == "not_found":
            return ("I couldn't find anything about that in your documents, so I can't answer it from them. "
                    "(I won't guess.)")
        heading = ("From your documents:" if self.status == "found"
                   else "I didn't find a clear answer in your documents. The closest passages are:")
        lines = [heading]
        for quote in self.quotes:
            lines.append(f"  “{quote.text}” [{self._number(quote)}]")
        for note in self.notes:
            lines.append(f"\nNote: {note}")
        lines.append("")
        lines.append(numbered(self.sources))
        return "\n".join(lines)

    def _number(self, quote: Quote) -> int:
        return self.sources.index(quote.source) + 1


def _weights(index: KnowledgeIndex, terms: list[str]) -> dict[str, float]:
    """IDF weight per (stemmed) query term: rare words matter more."""
    total = max(index.passage_count(), 1)
    weights = {}
    for term in terms:
        df = index.document_frequency(term)
        weights[_stem(term)] = math.log((total + 1) / (df + 0.5)) + 0.1
    return weights


def coverage(sentence: str, weights: dict[str, float]) -> float:
    if not weights:
        return 0.0
    found = stems(sentence)
    return sum(w for t, w in weights.items() if t in found) / sum(weights.values())


def _candidates(hits: list[Hit], weights: dict[str, float]) -> list[Quote]:
    quotes = []
    for hit in hits:
        for sentence in split_sentences(hit.text):
            if len(sentence.split()) < MIN_WORDS:
                continue
            quotes.append(Quote(sentence, hit.document, hit.page, coverage(sentence, weights)))
    quotes.sort(key=lambda q: -q.score)
    return quotes


def _distinct(quotes: list[Quote], limit: int) -> list[Quote]:
    chosen = []
    for quote in quotes:
        words = stems(quote.text)
        if any(len(words & stems(c.text)) / max(len(words), 1) > 0.8 for c in chosen):
            continue                         # nearly the same sentence twice
        chosen.append(quote)
        if len(chosen) == limit:
            break
    return chosen


def figures(sentence: str) -> list[tuple[str, str]]:
    """Numbers with the word after them: [('27', 'percent'), ('70', 'km')]."""
    found = []
    for value, unit in _FIGURE.findall(sentence):
        unit = _UNIT_WORDS.get((unit or "").lower(), (unit or "").lower())
        if unit:
            found.append((value.replace(",", ""), unit))
    return found


def disagreements(quotes: list[Quote], label=None) -> list[str]:
    """Different figures for the same unit from *different* sources, in
    sentences clearly about the question (score >= FOUND). Within one
    source, different numbers usually describe different things (dark
    matter 27 %, dark energy 68 %), so those are never reported."""
    label = label or (lambda q: q.document + (f" p. {q.page}" if q.page else ""))
    by_unit: dict[str, dict[str, set[str]]] = {}
    for quote in quotes:
        if quote.score < FOUND:
            continue
        for value, unit in figures(quote.text):
            by_unit.setdefault(unit, {}).setdefault(value, set()).add(quote.document)
    notes = []
    for unit, values in by_unit.items():
        documents = {d for where in values.values() for d in where}
        if len(values) > 1 and len(documents) > 1:
            parts = []
            for value, where in values.items():
                names = ", ".join(label(q) for q in quotes if q.document in where and (value, unit) in figures(q.text))
                parts.append(f"{value} {unit} ({', '.join(dict.fromkeys(names.split(', ')))})")
            notes.append("the sources give different figures: " + " vs ".join(parts) + ".")
    return notes


def answer(index: KnowledgeIndex, question: str, document: str | None = None) -> Answer:
    if not index.documents_indexed():
        return Answer("no_documents")
    terms = query_terms(question)
    if not terms:
        return Answer("empty_question")
    weights = _weights(index, terms)
    quotes = _candidates(index.search(question, limit=8, document=document), weights)
    if not quotes or quotes[0].score < PARTIAL:
        return Answer("not_found")
    best = quotes[0].score
    status = "found" if best >= FOUND else "partial"
    floor = max(PARTIAL, best * 0.75)
    chosen = _distinct([q for q in quotes if q.score >= floor], MAX_SENTENCES)
    sources = []
    for quote in chosen:
        if quote.source not in sources:
            sources.append(quote.source)
    return Answer(status, chosen, sources, disagreements(chosen) if len(chosen) > 1 else [])


# --------------------------------------------------------------- summaries


@dataclass
class Summary:
    title: str
    quotes: list[Quote]
    sources: list[Source]

    def text(self) -> str:
        if not self.quotes:
            return f"I couldn't find enough text to summarise {self.title}."
        lines = [f"Summary of {self.title} (quoted sentences):"]
        for quote in self.quotes:
            where = f" [{self.sources.index(quote.source) + 1}]"
            lines.append(f"  - {quote.text}{where}")
        lines += ["", numbered(self.sources)]
        return "\n".join(lines)


def _centrality(quotes: list[Quote]) -> None:
    """Score sentences by how typical their words are of the whole text."""
    counts = Counter(w for q in quotes for w in stems(q.text) if len(w) > 3)
    for quote in quotes:
        words = [w for w in stems(quote.text) if len(w) > 3]
        quote.score = sum(counts[w] for w in words) / math.sqrt(len(words) or 1)


def _pick_in_order(quotes: list[Quote], limit: int) -> list[Quote]:
    chosen = _distinct(sorted(quotes, key=lambda q: -q.score), limit)
    order = {id(q): n for n, q in enumerate(quotes)}
    return sorted(chosen, key=lambda q: order[id(q)])


def _sources_of(quotes: list[Quote]) -> list[Source]:
    sources = []
    for quote in quotes:
        if quote.source not in sources:
            sources.append(quote.source)
    return sources


def summarize_document(index: KnowledgeIndex, name: str, limit: int = 5) -> Summary:
    quotes = [Quote(s, hit.document, hit.page, 0.0)
              for hit in index.passages_of(name) for s in split_sentences(hit.text)
              if MIN_WORDS + 1 <= len(s.split()) <= 60]
    _centrality(quotes)
    chosen = _pick_in_order(quotes, max(2, min(limit, len(quotes) // 3 or 2)))
    return Summary(name, chosen, _sources_of(chosen))


def summarize_topic(index: KnowledgeIndex, topic: str, limit: int = 5) -> Summary:
    terms = query_terms(topic)
    weights = _weights(index, terms) if terms else {}
    quotes = [q for q in _candidates(index.search(topic, limit=10), weights) if q.score >= PARTIAL]
    strong = [q for q in quotes if q.score >= FOUND]
    if len(strong) >= 2:
        quotes = strong          # enough sentences squarely about the topic; skip the loosely related
    relevance = {id(q): q.score for q in quotes}
    _centrality(quotes)
    top = max((q.score for q in quotes), default=1) or 1
    for quote in quotes:
        quote.score = 0.7 * relevance[id(quote)] + 0.3 * quote.score / top
    chosen = _distinct(sorted(quotes, key=lambda q: -q.score), limit)
    return Summary(f"what your documents say about {topic}", chosen, _sources_of(chosen))


# --------------------------------------------------------------- comparison


@dataclass
class Side:
    name: str                      # document name or "Wikipedia"
    quotes: list[Quote]
    source: Source | None = None   # for live sources
    problem: str = ""


@dataclass
class Comparison:
    topic: str
    sides: list[Side]
    notes: list[str]

    def text(self) -> str:
        sources: list[Source] = []
        for side in self.sides:
            for quote in side.quotes:
                src = side.source or quote.source
                if src not in sources:
                    sources.append(src)
        lines = [f"What each source says about {self.topic}:"]
        for side in self.sides:
            lines.append(f"\n{side.name}:")
            if side.problem:
                lines.append(f"  ({side.problem})")
            for quote in side.quotes:
                src = side.source or quote.source
                lines.append(f"  “{quote.text}” [{sources.index(src) + 1}]")
        if self.notes:
            lines.append("")
            lines += [f"Note: {n}" for n in self.notes]
        elif all(side.quotes for side in self.sides):
            lines.append("\nI found no conflicting figures between them.")
        if sources:
            lines += ["", numbered(sources)]
        return "\n".join(lines)


def best_quotes_from_text(text: str, topic: str, name: str, weights: dict[str, float], limit: int = 2) -> list[Quote]:
    quotes = [Quote(s, name, None, coverage(s, weights)) for s in split_sentences(text) if len(s.split()) >= MIN_WORDS]
    quotes = ([q for q in quotes if q.score >= FOUND] or [q for q in quotes if q.score >= PARTIAL]
              or sorted(quotes, key=lambda q: -q.score)[:1])
    return _distinct(sorted(quotes, key=lambda q: -q.score), limit)


def compare(index: KnowledgeIndex, topic: str, sides: list[str], fetch_live=None) -> Comparison:
    """sides: document names, or "wikipedia" (then fetch_live(topic) -> (text, Source) or raises)."""
    terms = query_terms(topic)
    weights = _weights(index, terms) if terms else {}
    built = []
    for name in sides:
        if name.lower() == "wikipedia":
            try:
                text, source = fetch_live(topic)
                built.append(Side("Wikipedia", best_quotes_from_text(text, topic, "Wikipedia", weights), source))
            except Exception as error:
                built.append(Side("Wikipedia", [], None, str(error)))
            continue
        document = None if name == "documents" else name          # "documents" = all of them
        quotes = [q for q in _candidates(index.search(topic, limit=6, document=document), weights)
                  if q.score >= PARTIAL]
        strong = [q for q in quotes if q.score >= FOUND]
        side = Side(name, _distinct(strong or quotes, 2))
        if not side.quotes:
            side.problem = f"{name} doesn't seem to mention {topic}"
        built.append(side)
    all_quotes = [q for side in built for q in side.quotes]
    label = lambda q: q.document  # noqa: E731
    notes = disagreements(all_quotes, label) if len(built) > 1 else []
    return Comparison(topic, built, notes)
