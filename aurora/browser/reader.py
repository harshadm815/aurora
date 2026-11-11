"""
Reader mode: the article in a page, without menus, adverts, cookie
banners, scripts and forms.

Works on the page's HTML (from the browser), with Python's own HTML
parser; nothing is downloaded. The steps:

1. Skip everything that is never article text: script, style, nav,
   header, footer, aside, form, buttons, menus, iframes, and elements
   whose class or id says they are navigation, adverts, cookie notices,
   share buttons, comments or related links.
2. Collect blocks: headings, paragraphs, list items, quotes, code,
   table cells, with how much of each block is link text.
3. If the page marks its article (<article> or <main>, or role="main")
   and that holds enough text, keep only that.
4. Drop blocks that are mostly links (menus that weren't marked) and
   very short fragments.

It's a heuristic, like every browser's reader mode: some pages lose a
paragraph or keep a stray line. The original page is always one click
away.
"""

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser

SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas", "nav", "header", "footer", "aside",
             "form", "button", "select", "option", "iframe", "object", "embed", "dialog", "menu", "figure"}
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "td", "th", "dd", "dt",
              "figcaption", "caption"}
CONTAINERS = {"div", "section", "article", "main", "body", "ul", "ol", "table", "tbody", "tr", "dl"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

_NOISE = re.compile(
    r"(?:^|[\s_-])(?:nav|navbar|menu|breadcrumbs?|sidebar|footer|header|masthead|banner|ads?|advert\w*|"
    r"sponsor\w*|promo\w*|cookies?|consent|gdpr|newsletter|subscribe|signup|share|sharing|social|comments?|"
    r"related|recommended|popular|trending|outbrain|taboola|paywall|modal|popup|skip-link)(?:$|[\s_-])", re.I)

MIN_ARTICLE_WORDS = 80        # a marked <article>/<main> with less than this isn't trusted
MIN_BLOCK_WORDS = 4           # shorter non-heading blocks are dropped (bylines survive via headings)
LINK_HEAVY = 0.6              # blocks with more link text than this share are menus


@dataclass
class Block:
    kind: str                 # "h1".."h6", "p", "li", "quote", "code", "cell"
    text: str
    links: int = 0            # characters of link text
    in_main: bool = False

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def heading(self) -> bool:
        return self.kind in HEADINGS


@dataclass
class ReaderPage:
    url: str
    title: str
    description: str = ""
    blocks: list[Block] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Plain text: headings and paragraphs on their own lines."""
        return "\n\n".join(b.text for b in self.blocks)

    @property
    def words(self) -> int:
        return sum(b.words for b in self.blocks)

    @property
    def outline(self) -> list[str]:
        return [b.text for b in self.blocks if b.heading]

    def markdown(self) -> str:
        lines = []
        for block in self.blocks:
            if block.heading:
                lines.append("#" * min(int(block.kind[1]) + 1, 6) + " " + block.text)
            elif block.kind == "li":
                lines.append("- " + block.text)
            elif block.kind == "quote":
                lines.append("> " + block.text)
            elif block.kind == "code":
                lines.append("```\n" + block.text + "\n```")
            else:
                lines.append(block.text)
        return "\n\n".join(lines)


def _noisy(attrs: dict) -> bool:
    if "hidden" in attrs or attrs.get("aria-hidden") == "true":      # <p hidden> has no value
        return True
    if re.search(r"display\s*:\s*none", attrs.get("style") or "", re.I):
        return True
    if attrs.get("role") in ("navigation", "banner", "contentinfo", "complementary", "search", "dialog"):
        return True
    return bool(_NOISE.search(" ".join(filter(None, (attrs.get("class"), attrs.get("id"))))))


class _Parser(HTMLParser):

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool]] = []     # (tag, skipping, main)
        self.blocks: list[Block] = []
        self.current: Block | None = None
        self.current_tag = ""
        self.link_depth = 0
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta: dict[str, str] = {}

    @property
    def skipping(self) -> bool:
        return bool(self.stack) and self.stack[-1][1]

    @property
    def in_main(self) -> bool:
        return bool(self.stack) and self.stack[-1][2]

    def handle_starttag(self, tag, attrs):
        attrs = {k: (v or "") if v is not None else None for k, v in attrs}
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key in ("og:title", "description", "og:description", "twitter:title"):
                self.meta.setdefault(key, attrs.get("content") or "")
            return
        if tag == "title" and not self.stack_has("svg"):
            self.in_title = True
            return
        if tag in VOID:
            if tag == "br" and self.current is not None:
                self.current.text += " "
            return
        skipping = self.skipping or tag in SKIP_TAGS or _noisy(attrs)
        main = self.in_main or tag in ("article", "main") or attrs.get("role") == "main"
        self.stack.append((tag, skipping, main))
        if skipping:
            return
        if tag == "a":
            self.link_depth += 1
        if tag in BLOCK_TAGS:
            self._finish()
            kind = {"blockquote": "quote", "pre": "code", "td": "cell", "th": "cell", "dd": "p", "dt": "p",
                    "figcaption": "p", "caption": "p"}.get(tag, tag)
            self.current, self.current_tag = Block(kind, "", 0, main), tag
        elif tag in CONTAINERS:
            self._finish()

    def stack_has(self, tag: str) -> bool:
        return any(t == tag for t, _, _ in self.stack)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            return
        if tag in VOID:
            return
        if not any(t == tag for t, _, _ in self.stack):
            return                                  # a stray end tag
        while self.stack:
            open_tag, skipping, _ = self.stack.pop()
            if not skipping and open_tag == "a":
                self.link_depth = max(0, self.link_depth - 1)
            if not skipping and open_tag == self.current_tag:
                self._finish()
            if open_tag == tag:
                break
        if tag in CONTAINERS and not self.skipping:
            self._finish()

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
            return
        if self.skipping:
            return
        if self.current is None:
            if not data.strip():
                return
            # Text directly in a <div>: a block of its own.
            self.current, self.current_tag = Block("p", "", 0, self.in_main), ""
        if self.current.kind == "code":
            self.current.text += data
        else:
            self.current.text += data
        if self.link_depth:
            self.current.links += len(data.strip())

    def _finish(self):
        block, self.current, self.current_tag = self.current, None, ""
        if block is None:
            return
        if block.kind != "code":
            block.text = " ".join(unescape(block.text).split())
        else:
            block.text = block.text.strip("\n")
        if block.text:
            self.blocks.append(block)

    def close(self):
        super().close()
        self._finish()


def _clean_title(title: str) -> str:
    return " ".join(unescape(title or "").split())


def extract(html: str, url: str = "") -> ReaderPage:
    parser = _Parser()
    parser.feed(html or "")
    parser.close()
    blocks = parser.blocks

    main = [b for b in blocks if b.in_main]
    if sum(b.words for b in main) >= MIN_ARTICLE_WORDS:
        blocks = main

    kept = []
    for block in blocks:
        if block.heading:
            if block.links < len(block.text) * LINK_HEAVY or block.kind == "h1":
                kept.append(block)
            continue
        if block.kind != "code" and block.words < MIN_BLOCK_WORDS:
            continue
        if block.links > len(block.text) * LINK_HEAVY:
            continue
        kept.append(block)
    # Headings with nothing after them (end of the article, or menus) are dropped.
    while kept and kept[-1].heading:
        kept.pop()
    # The same text twice in a row (a summary repeated as the first paragraph).
    deduped = [b for i, b in enumerate(kept) if i == 0 or b.text != kept[i - 1].text]

    title = (_clean_title(parser.meta.get("og:title", "")) or _clean_title("".join(parser.title_parts))
             or next((b.text for b in deduped if b.kind == "h1"), "") or url)
    description = _clean_title(parser.meta.get("description") or parser.meta.get("og:description") or "")
    return ReaderPage(url, title, description, deduped)
