"""
Where news comes from. Each source turns a NewsQuery into Articles, or says
why it couldn't (not set up, outside its coverage, refused, unreachable).

- GDELT: a worldwide index of news sites; free, no key; about the last
  3 months; one request every 5 seconds.
- Google News (RSS search): free, no key; links go through Google News.
- Publisher feeds (settings.NEWS_FEEDS): downloaded whole, searched here,
  so no search terms are sent to publishers.
- Web news search (Brave Search API): only with NEWS_WEB_SEARCH_KEY.

All requests go through the network gate (component "News"). Only the
search terms (topic and place) are sent. Article pages are never fetched.
"""

import html
import logging
import re
import threading
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from aurora.config import settings
from aurora.network import NetworkBlocked, gate
from aurora.news.article import FEED, GDELT, GOOGLE_NEWS, WEB, Article
from aurora.news.query import NewsQuery
from aurora.tools.untrusted import one_line, shorten

logger = logging.getLogger(__name__)

COMPONENT = "News"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_SEARCH_URL = "https://news.google.com/rss/search"
GOOGLE_TOP_URL = "https://news.google.com/rss"
BRAVE_URL = "https://api.search.brave.com/res/v1/news/search"
GDELT_DAYS = 90                 # GDELT's article search covers about 3 months
GDELT_INTERVAL = 5.0            # its rule: one request every 5 seconds
MAX_FEED_BYTES = 4_000_000
NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "content": "http://purl.org/rss/1.0/modules/content/", "media": "http://search.yahoo.com/mrss/"}


class SourceError(Exception):
    """Something to tell the user about this source."""


@dataclass
class SourceResult:
    source: str                         # "GDELT", "Google News", "BBC News (feed)"…
    status: str                         # "ok", "skipped", "failed", "blocked"
    articles: list[Article] = field(default_factory=list)
    note: str = ""                      # why skipped / failed, or a coverage remark
    blocked: NetworkBlocked | None = None


def user_agent() -> str:
    # No contact details: news services don't need to know who you are.
    return f"{settings.APP_NAME}/{settings.VERSION} (personal news reader)"


def local_time(value: datetime | None) -> datetime | None:
    """Every date AURORA shows is in this PC's time zone (naive)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().replace(tzinfo=None)


def clean(text: str | None, limit: int = 400) -> str:
    """Publisher text is untrusted: tags removed, entities decoded, one line, shortened."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    return shorten(one_line(html.unescape(html.unescape(text))), limit)


def site(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class _Source:
    name = "source"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = gate.client(COMPONENT, timeout=settings.NEWS_TIMEOUT_SECONDS,
                                       headers={"User-Agent": user_agent()}, follow_redirects=True)
        return self._client

    def _get(self, url: str, **kwargs) -> httpx.Response:
        try:
            response = self.client.get(url, **kwargs)
        except NetworkBlocked:
            raise
        except httpx.TimeoutException:
            raise SourceError("didn't answer in time") from None
        except httpx.HTTPError:
            raise SourceError("couldn't be reached") from None
        if response.status_code == 429:
            raise SourceError("is limiting requests right now; try again in a minute")
        if response.status_code in (401, 403):
            raise SourceError(f"refused the request (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise SourceError(f"returned an error (HTTP {response.status_code})")
        if len(response.content) > MAX_FEED_BYTES:
            raise SourceError("sent more data than AURORA accepts")
        return response

    def search(self, query: NewsQuery) -> SourceResult:
        try:
            return self._search(query)
        except NetworkBlocked as blocked:
            return SourceResult(self.name, "blocked", note=blocked.reason, blocked=blocked)
        except SourceError as error:
            return SourceResult(self.name, "failed", note=f"{self.name} {error}")
        except (ValueError, ElementTree.ParseError) as error:
            logger.warning("%s sent data AURORA couldn't read (%s)", self.name, type(error).__name__)
            return SourceResult(self.name, "failed", note=f"{self.name} sent data AURORA couldn't read")

    def _search(self, query: NewsQuery) -> SourceResult:
        raise NotImplementedError


# --- GDELT -----------------------------------------------------------------------

class GdeltSource(_Source):
    name = GDELT
    _lock = threading.Lock()
    _last = 0.0

    def __init__(self, client=None, clock=time.monotonic, sleep=time.sleep, today=None):
        super().__init__(client)
        self.clock, self.sleep, self.today = clock, sleep, today

    @staticmethod
    def _terms(terms: str) -> str:
        # GDELT rejects words under 3 letters outside quotes ("AI" → "AI" in quotes is accepted).
        words = []
        for word in re.findall(r'\(|\)|"[^"]+"|\S+', terms):
            if word.upper() == "OR" or word in "()" or word.startswith('"') or len(word) >= 3:
                words.append(word)
            else:
                words.append(f'"{word}"')
        return " ".join(words)

    def _search(self, query):
        terms = query.terms()
        if not terms:
            return SourceResult(self.name, "skipped", note="GDELT needs search terms (it has no top-stories list)")
        today = self.today or datetime.now().date()
        if query.until and query.until < today - timedelta(days=GDELT_DAYS):
            return SourceResult(self.name, "skipped", note="GDELT's search covers only about the last 3 months")
        since = max(query.since or today - timedelta(days=7), today - timedelta(days=GDELT_DAYS))
        until = (query.until or today) + timedelta(days=1)
        params = {"query": self._terms(terms), "mode": "ArtList", "format": "json", "sort": "DateDesc",
                  "maxrecords": 75, "startdatetime": f"{since:%Y%m%d}000000",
                  "enddatetime": f"{until:%Y%m%d}000000"}
        with GdeltSource._lock:                       # GDELT's limit: one request every 5 s
            wait = GDELT_INTERVAL - (self.clock() - GdeltSource._last)
            if 0 < wait <= GDELT_INTERVAL:
                self.sleep(wait)
            try:
                response = self._get(GDELT_URL, params=params)
            finally:
                GdeltSource._last = self.clock()
        text = response.text.strip()
        if not text.startswith("{"):
            # GDELT explains a rejected query in plain text ("phrase too short"…).
            raise SourceError(f"didn't accept the search ({clean(text, 120)})")
        data = response.json() if text else {}
        articles = []
        for item in data.get("articles") or []:
            url = item.get("url") or ""
            title = clean(item.get("title"), 300)
            if not url.startswith(("http://", "https://")) or not title:
                continue
            seen = item.get("seendate") or ""
            try:
                published = local_time(datetime.strptime(seen, "%Y%m%dT%H%M%SZ"))
            except ValueError:
                published = None
            domain = site(url) or (item.get("domain") or "").lower()
            articles.append(Article(title=title, publisher=domain, url=url, method=GDELT, published=published,
                                    date_note="time GDELT first saw it", domain=domain,
                                    country=item.get("sourcecountry") or "", language=item.get("language") or ""))
        return SourceResult(self.name, "ok", articles)


# --- Google News -------------------------------------------------------------------

class GoogleNewsSource(_Source):
    name = GOOGLE_NEWS

    def _search(self, query):
        country, language = (settings.NEWS_EDITION.split(":") + ["en"])[:2]
        edition = {"hl": f"{language}-{country}", "gl": country, "ceid": f"{country}:{language}"}
        terms = query.terms()
        if terms:
            dated = terms
            if query.since:
                dated += f" after:{query.since - timedelta(days=1):%Y-%m-%d}"
            if query.until:
                dated += f" before:{query.until + timedelta(days=1):%Y-%m-%d}"
            response = self._get(GOOGLE_SEARCH_URL, params={"q": dated, **edition})
        else:
            response = self._get(GOOGLE_TOP_URL, params=edition)
        root = ElementTree.fromstring(response.content)
        articles = []
        for item in root.iter("item"):
            source = item.find("source")
            publisher = one_line(source.text if source is not None else "")
            title = clean(item.findtext("title"), 300)
            if publisher and title.endswith(f" - {publisher}"):
                title = title[: -len(publisher) - 3].strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link.startswith("https://"):
                continue
            home = source.get("url", "") if source is not None else ""
            articles.append(Article(title=title, publisher=publisher or "unknown publisher", url=link,
                                    method=GOOGLE_NEWS, published=_rss_date(item.findtext("pubDate")),
                                    domain=site(home), original_url_known=False))
        return SourceResult(self.name, "ok", articles)


# --- Publisher feeds ------------------------------------------------------------

def _rss_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return local_time(parsedate_to_datetime(text.strip()))
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return local_time(datetime.fromisoformat(text.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def parse_feed(content: bytes, feed: dict) -> list[Article]:
    """Articles in an RSS 2.0 or Atom feed."""
    root = ElementTree.fromstring(content)
    name = feed.get("name") or site(feed.get("url", ""))
    official = bool(feed.get("official"))
    articles = []
    items = list(root.iter("item"))
    if items:
        for item in items:
            link = (item.findtext("link") or item.findtext("guid") or "").strip()
            author = item.findtext("dc:creator", namespaces=NS) or item.findtext("author") or ""
            articles.append(Article(
                title=clean(item.findtext("title"), 300), publisher=name, url=link, method=FEED,
                published=_rss_date(item.findtext("pubDate") or item.findtext("dc:date", namespaces=NS)),
                summary=clean(item.findtext("description"), 500), author=clean(author, 80),
                domain=site(link) or site(feed.get("url", "")), country=feed.get("country", ""), official=official))
    else:
        for entry in root.iter(f"{{{NS['atom']}}}entry"):
            # (An Element without children is falsy, so no `or` here.)
            link_element = entry.find("atom:link[@rel='alternate']", NS)
            if link_element is None:
                link_element = entry.find("atom:link", NS)
            link = link_element.get("href", "") if link_element is not None else ""
            articles.append(Article(
                title=clean(entry.findtext("atom:title", namespaces=NS), 300), publisher=name, url=link,
                method=FEED,
                published=_rss_date(entry.findtext("atom:published", namespaces=NS)
                                    or entry.findtext("atom:updated", namespaces=NS)),
                summary=clean(entry.findtext("atom:summary", namespaces=NS)
                              or entry.findtext("atom:content", namespaces=NS), 500),
                author=clean(entry.findtext("atom:author/atom:name", namespaces=NS), 80),
                domain=site(link), country=feed.get("country", ""), official=official))
    return [a for a in articles if a.title and a.url.startswith(("http://", "https://"))]


class FeedSource(_Source):
    """One publisher feed."""

    def __init__(self, feed: dict, client=None):
        super().__init__(client)
        self.feed = feed
        self.name = f"{feed.get('name') or site(feed['url'])} (feed)"

    def _search(self, query):
        response = self._get(self.feed["url"])
        return SourceResult(self.name, "ok", parse_feed(response.content, self.feed))


# --- Web news search ---------------------------------------------------------------

class WebSearchSource(_Source):
    name = WEB

    def __init__(self, client=None, key: str | None = None):
        super().__init__(client)
        self.key = settings.NEWS_WEB_SEARCH_KEY if key is None else key

    def _search(self, query):
        if not self.key:
            return SourceResult(self.name, "skipped",
                                note="web news search isn't set up (needs NEWS_WEB_SEARCH_KEY in local_settings.py)")
        terms = query.terms()
        if not terms:
            return SourceResult(self.name, "skipped", note="web search needs search terms")
        params = {"q": terms, "count": 20}
        if query.since and query.until:
            params["freshness"] = f"{query.since:%Y-%m-%d}to{query.until:%Y-%m-%d}"
        response = self._get(BRAVE_URL, params=params,
                             headers={"X-Subscription-Token": self.key, "Accept": "application/json"})
        articles = []
        for item in response.json().get("results") or []:
            url = item.get("url") or ""
            if not url.startswith(("http://", "https://")):
                continue
            meta = item.get("meta_url") or {}
            published = None
            if item.get("page_age"):
                try:
                    published = local_time(datetime.fromisoformat(str(item["page_age"]).replace("Z", "+00:00")))
                except ValueError:
                    published = None
            source = item.get("source")
            publisher = (source.get("name") if isinstance(source, dict) else source) or meta.get("hostname") or site(url)
            articles.append(Article(title=clean(item.get("title"), 300), publisher=one_line(str(publisher)), url=url,
                                    method=WEB, published=published, summary=clean(item.get("description"), 500),
                                    domain=site(url)))
        return SourceResult(self.name, "ok", [a for a in articles if a.title])


def register_feed_hosts(feeds) -> None:
    """Publisher feeds you list in settings become News's destinations."""
    for feed in feeds:
        url = feed.get("url", "")
        if url.startswith(("http://", "https://")):
            gate.allow_host(COMPONENT, url)


def in_range(article: Article, since: date | None, until: date | None) -> bool:
    if article.published is None:
        return False
    day = article.published.date()
    return (since is None or day >= since) and (until is None or day <= until)
