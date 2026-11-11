"""
Questions about the page you're viewing, answered by quoting it.

The same method as answers from your documents (aurora/knowledge/answers.py):
the sentences on the page that contain most of the question's words
(rarer words count for more) are quoted, with the page as the source. If
no sentence covers enough of the question, AURORA says the page doesn't
seem to answer it. It never writes an answer of its own: the local
language model can't yet be trusted to restate a page faithfully.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime

from aurora.browser.reader import ReaderPage
from aurora.knowledge.answers import FOUND, MAX_SENTENCES, MIN_WORDS, PARTIAL, coverage, stems
from aurora.knowledge.index import query_terms, split_sentences
from aurora.knowledge.sources import LIVE, Source
from aurora.memory.manager import _stem


@dataclass
class PageAnswer:
    status: str                      # "found", "partial", "not_found", "empty_page", "empty_question"
    quotes: list[str] = field(default_factory=list)
    source: Source | None = None

    def text(self) -> str:
        if self.status == "empty_question":
            return "What would you like to know about this page?"
        if self.status == "empty_page":
            return ("I couldn't find readable text on this page (it may be mostly pictures, a PDF, "
                    "or still loading).")
        if self.status == "not_found":
            return "This page doesn't seem to answer that: no sentence on it covers your question. (I won't guess.)"
        heading = ("From this page:" if self.status == "found"
                   else "The page doesn't answer that clearly. The closest sentences are:")
        lines = [heading] + [f"  “{quote}”" for quote in self.quotes]
        if self.source is not None:
            lines += ["", "Source: " + self.source.citation()]
        return "\n".join(lines)


def page_source(page: ReaderPage, read: datetime | None = None) -> Source:
    return Source(LIVE, page.title or page.url, page.url, read or datetime.now())


def sentences(page: ReaderPage) -> list[str]:
    found = []
    for block in page.blocks:
        if block.heading or block.kind == "code":
            continue
        found += [s for s in split_sentences(block.text) if len(s.split()) >= MIN_WORDS]
    return found


def answer(page: ReaderPage, question: str, read: datetime | None = None) -> PageAnswer:
    terms = query_terms(question)
    if not terms:
        return PageAnswer("empty_question")
    candidates = sentences(page)
    if not candidates:
        return PageAnswer("empty_page")
    stemmed = [stems(s) for s in candidates]
    total = len(candidates)
    weights = {}
    for term in terms:
        stem = _stem(term)
        frequency = sum(1 for words in stemmed if stem in words)
        weights[stem] = math.log((total + 1) / (frequency + 0.5)) + 0.1
    scored = sorted(((coverage(s, weights), i, s) for i, s in enumerate(candidates)), key=lambda x: (-x[0], x[1]))
    best = scored[0][0]
    if best < PARTIAL:
        return PageAnswer("not_found")
    floor = max(PARTIAL, best * 0.75)
    chosen: list[tuple[int, str]] = []
    for score, position, sentence in scored:
        if score < floor:
            break
        words = stems(sentence)
        if any(len(words & stems(c)) / max(len(words), 1) > 0.8 for _, c in chosen):
            continue
        chosen.append((position, sentence))
        if len(chosen) == MAX_SENTENCES:
            break
    chosen.sort()                                # in the page's own order
    return PageAnswer("found" if best >= FOUND else "partial", [s for _, s in chosen], page_source(page, read))
