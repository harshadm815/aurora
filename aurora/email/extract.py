"""
What an email asks of you and when: found in its text, shown as "found in the email", never as facts.

    dates(text, today)     dates and times mentioned ("tomorrow at 10", "on 21 Nov", "by Friday")
    deadlines(text, today) dates that come with "by", "due", "deadline", "before", "no later than"
    requests(text)         sentences that ask you to do or answer something
    summary(text)          the few sentences that matter (no AI: instant, and it can't invent anything)
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], start=1)}
_TIME = r"(?:\s*(?:at|@)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}:\d{2}))?"
_PATTERNS = [
    re.compile(r"\b(today|tonight|tomorrow|day after tomorrow)\b" + _TIME, re.I),
    re.compile(r"\b(?:on\s+|this\s+|next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b" + _TIME, re.I),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+(\d{4}))?" + _TIME, re.I),
    re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?" + _TIME, re.I),
    re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b" + _TIME),
]
_DEADLINE = re.compile(r"\b(by|due|deadline|before|no later than|last date|latest by|submit by|until)\b", re.I)
_REQUEST = re.compile(r"(\?\s*$|\b(please|kindly|could you|can you|would you|let me know|let us know|confirm|"
                      r"reply|respond|send (me|us)|share|fill (in|out)|submit|sign|approve|review|rsvp|"
                      r"get back to)\b)", re.I)
_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
_QUOTED = re.compile(r"(?ims)^(on .{0,120}wrote:|-{2,}\s*original message|from:\s.+\nsent:).*")


@dataclass
class Found:
    when: date
    time: str
    text: str            # the words it came from


def _year(day: int, month: int, today: date) -> date | None:
    try:
        found = date(today.year, month, day)
    except ValueError:
        return None
    return found if found >= today - timedelta(days=60) else date(today.year + 1, month, day)


def dates(text: str, today: date) -> list[Found]:
    results = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            groups = [g for g in match.groups()]
            word = (groups[0] or "").lower()
            when = None
            if pattern is _PATTERNS[0]:
                when = today + timedelta(days={"today": 0, "tonight": 0, "tomorrow": 1, "day after tomorrow": 2}[word])
            elif pattern is _PATTERNS[1]:
                ahead = (_WEEKDAYS.index(word) - today.weekday()) % 7
                when = today + timedelta(days=ahead or (7 if "next" in match.group(0).lower() else 0))
            elif pattern is _PATTERNS[2]:
                when = (_year(int(groups[0]), _MONTHS[groups[1][:3].lower()], today) if not groups[2] else
                        _safe(int(groups[2]), _MONTHS[groups[1][:3].lower()], int(groups[0])))
            elif pattern is _PATTERNS[3]:
                when = (_year(int(groups[1]), _MONTHS[groups[0][:3].lower()], today) if not groups[2] else
                        _safe(int(groups[2]), _MONTHS[groups[0][:3].lower()], int(groups[1])))
            else:                                            # 21/11/2026: day first (India)
                year = int(groups[2]) + (2000 if len(groups[2]) == 2 else 0)
                when = _safe(year, int(groups[1]), int(groups[0]))
            if when:
                results.append(Found(when, (groups[-1] or "").strip(), match.group(0).strip()))
    unique = {}
    for found in results:
        unique.setdefault((found.when, found.time), found)
    return sorted(unique.values(), key=lambda f: (f.when, f.time))


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def own_text(body: str) -> str:
    """The new part of an email, without the quoted earlier messages below it."""
    return _QUOTED.sub("", body).strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCES.split(own_text(text)) if len(s.strip()) > 3]


def deadlines(text: str, today: date) -> list[Found]:
    """The date right after "by", "due", "deadline"… in each sentence (not every date the sentence has)."""
    found = []
    for sentence in sentences(text):
        for word in _DEADLINE.finditer(sentence):
            after = dates(sentence[word.start():], today)
            if after:
                first = min(after, key=lambda f: sentence.find(f.text, word.start()))
                if first not in found:
                    found.append(first)
    return found


def requests(text: str, limit: int = 4) -> list[str]:
    asks = [s for s in sentences(text) if _REQUEST.search(s) and len(s) < 300]
    return asks[:limit]


def summary(text: str, limit: int = 3) -> list[str]:
    """The sentences that matter most: the opening, requests and anything with a date, in their order."""
    parts = sentences(text)
    if not parts:
        return []
    scored = []
    for i, s in enumerate(parts):
        score = (2 if i == 0 else 0) + (2 if _REQUEST.search(s) else 0) + (2 if any(p.search(s) for p in _PATTERNS) else 0)
        score += 1 if 20 < len(s) < 220 else 0
        scored.append((score, i, s))
    best = sorted(sorted(scored, reverse=True)[:limit], key=lambda t: t[1])
    return [s if len(s) <= 240 else s[:239] + "…" for _, _, s in best]
