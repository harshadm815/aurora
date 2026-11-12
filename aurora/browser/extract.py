"""
The AURORA panel's page tools, all without a language model, so every
result can be checked against the page:

    summary(page)          key sentences, quoted in the page's order (the same
                           method as document summaries in Knowledge)
    details(page, html)    important information: published date, author,
                           dates and figures mentioned, headings, tables, links
    compare(pages)         open tabs side by side: site, title, date, length,
                           headings, key sentences, and figures that differ

Written explanations need a capable local model; "Explain selection"
sends the selected text to Chat, where the answer is labelled as the
language model's.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from aurora.browser.answers import sentences
from aurora.browser.reader import ReaderPage
from aurora.knowledge.answers import Quote, _centrality, _pick_in_order, figures

MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
          "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_DATE = re.compile(
    rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})\.?,?\s+\d{{4}}|(?:{MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}|"
    rf"(?:{MONTHS})\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}})\b", re.I)
_YEAR = re.compile(r"\b(?:in|since|by|until|from|during|of|before|after)\s+((?:1[5-9]|20)\d{2})\b", re.I)
NOT_UNITS = {"and", "or", "the", "of", "to", "in", "a", "an", "with", "by", "from", "for", "at", "on", "as", "is",
             "was", "were", "are", "that", "this", "it", "its", "who", "which", "when", "after", "before", "than"}
MAX_LINKS = 40
MAX_TABLES = 5


# --- Summary ---------------------------------------------------------------------------

@dataclass
class PageSummary:
    title: str
    url: str
    sentences: list[str]

    def text(self) -> str:
        if not self.sentences:
            return "There isn't enough text on this page to pick out key sentences."
        lines = [f"Key sentences from “{self.title}” (quoted, in the page's order):"]
        lines += [f"  • {s}" for s in self.sentences]
        lines += ["", f"Source: {self.url}",
                  "These are the page's own sentences, chosen for how central they are, not a summary "
                  "written by AURORA."]
        return "\n".join(lines)


def summary(page: ReaderPage, limit: int = 5) -> PageSummary:
    quotes = [Quote(s, page.title, None, 0.0) for s in sentences(page) if len(s.split()) <= 60]
    if len(quotes) < 2:
        return PageSummary(page.title, page.url, [q.text for q in quotes])
    _centrality(quotes)
    chosen = _pick_in_order(quotes, max(2, min(limit, len(quotes) // 3 or 2)))
    return PageSummary(page.title, page.url, [q.text for q in chosen])


# --- Important information ---------------------------------------------------------------

class _Details(HTMLParser):
    """Meta dates and authors, links and tables from the raw HTML."""

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.meta: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []
        self.tables: list[list[list[str]]] = []
        self._link: list[str] | None = None
        self._href = ""
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style", "noscript", "template"):
            self._skip += 1
        elif tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
            if key in ("article:published_time", "datepublished", "date", "dc.date", "article:modified_time",
                       "author", "article:author", "dc.creator", "og:site_name"):
                self.meta.setdefault(key, (attrs.get("content") or "").strip())
        elif tag == "time" and attrs.get("datetime"):
            self.meta.setdefault("time", attrs["datetime"].strip())
        elif tag == "a" and attrs.get("href"):
            self._link, self._href = [], attrs["href"]
        elif tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
            self.tables[-1].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._link is not None:
            text = " ".join("".join(self._link).split())
            url = urljoin(self.base, self._href)
            if text and urlsplit(url).scheme in ("http", "https"):
                self.links.append((text[:120], url))
            self._link = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split())[:80])
            self._cell = None
        elif tag == "tr":
            self._row = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._link is not None:
            self._link.append(data)
        if self._cell is not None:
            self._cell.append(data)


@dataclass
class PageDetails:
    title: str
    url: str
    published: str = ""
    author: str = ""
    site: str = ""
    dates: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)

    def text(self) -> str:
        lines = [f"Important information from “{self.title}”", f"Address: {self.url}"]
        for name, value in (("Site", self.site), ("Published", self.published), ("Author", self.author)):
            lines.append(f"{name}: {value or 'not given on the page'}")
        if self.dates:
            lines.append("Dates mentioned: " + ", ".join(self.dates))
        if self.figures:
            lines.append("Figures mentioned: " + ", ".join(self.figures))
        if self.headings:
            lines.append("Sections: " + " · ".join(self.headings))
        for number, table in enumerate(self.tables, start=1):
            lines.append(f"Table {number} ({len(table)} rows): " + " | ".join(table[0]) if table and table[0] else "")
        if self.links:
            outside = [(t, u) for t, u in self.links if urlsplit(u).hostname != urlsplit(self.url).hostname]
            lines.append(f"Links: {len(self.links)} ({len(outside)} to other sites)")
        return "\n".join(line for line in lines if line)


def details(page: ReaderPage, html: str) -> PageDetails:
    parser = _Details(page.url)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:                 # damaged markup: keep what was read
        pass
    meta = parser.meta
    published = (meta.get("article:published_time") or meta.get("datepublished") or meta.get("date")
                 or meta.get("dc.date") or meta.get("time") or "")
    dates = [m.group(0) for m in _DATE.finditer(page.text)] + [m.group(1) for m in _YEAR.finditer(page.text)]
    found_dates = list(dict.fromkeys(dates))[:12]
    years = {m.group(1) for m in _YEAR.finditer(page.text)}
    found_figures = []
    for sentence in sentences(page):
        for value, unit in figures(sentence):
            if unit not in NOT_UNITS and value not in years:
                found_figures.append(f"{value} {unit}")
    tables = [t for t in parser.tables if len(t) >= 2 and any(len(row) >= 2 for row in t)][:MAX_TABLES]
    seen, links = set(), []
    for text, url in parser.links:
        if url not in seen:
            seen.add(url)
            links.append((text, url))
    return PageDetails(page.title, page.url, published[:40], (meta.get("author") or meta.get("article:author")
                       or meta.get("dc.creator") or "")[:120], meta.get("og:site_name", "")[:80], found_dates,
                       list(dict.fromkeys(found_figures))[:15], page.outline[:15], tables, links[:MAX_LINKS])


# --- Comparing open tabs -----------------------------------------------------------------

@dataclass
class Comparison:
    pages: list[tuple[ReaderPage, PageDetails, PageSummary]]
    differing: list[str]

    def text(self) -> str:
        if len(self.pages) < 2:
            return "Open at least two web pages in tabs to compare them."
        lines = [f"Comparing {len(self.pages)} open pages (their own words; nothing here is written by AURORA):", ""]
        for number, (page, info, short) in enumerate(self.pages, start=1):
            host = urlsplit(page.url).hostname or page.url
            lines.append(f"[{number}] {page.title}")
            lines.append(f"    {host} · published {info.published or 'date not given'} · {page.words} words")
            if info.headings:
                lines.append("    Sections: " + " · ".join(info.headings[:6]))
            for sentence in short.sentences[:3]:
                lines.append(f"    • {sentence}")
            lines.append("")
        if self.differing:
            lines.append("Figures that differ between the pages:")
            lines += [f"  • {d}" for d in self.differing]
        else:
            lines.append("No figures with the same unit differ between the pages.")
        return "\n".join(lines)


def compare(pages: list[tuple[ReaderPage, str]]) -> Comparison:
    rows = [(page, details(page, html), summary(page, 3)) for page, html in pages]
    by_unit: dict[str, dict[str, set[int]]] = {}
    for number, (page, _, _) in enumerate(rows, start=1):
        for sentence in sentences(page):
            for value, unit in figures(sentence):
                if unit not in NOT_UNITS and (len(unit) > 2 or unit in ("km", "kg", "cm", "mm", "%")):
                    by_unit.setdefault(unit, {}).setdefault(value, set()).add(number)
    differing = []
    for unit, values in by_unit.items():
        pages_with = {n for where in values.values() for n in where}
        if len(values) > 1 and len(pages_with) > 1 and len(values) <= 4:
            differing.append(f"{unit}: " + "; ".join(f"{value} ([{', '.join(map(str, sorted(where)))}])"
                                                     for value, where in values.items()))
    return Comparison(rows, differing[:10])
