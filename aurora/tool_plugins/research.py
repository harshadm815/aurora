"""
Research tools backed by free, key-less public APIs.

- WikipediaTool: encyclopedia summary of a topic (Wikipedia REST API).
- ArxivTool:     research papers on a topic (arXiv API).

Only the topic is sent. Everything returned is treated as untrusted
text: cleaned, shortened, shown with its source, never obeyed. If a
service can't be reached the tool says so; it never makes up an answer.

General web search is not included: every reliable provider needs an
account and API key. A ``WebSearchTool`` can be added later the same way.
"""

import logging
import re
import xml.etree.ElementTree as ElementTree
from urllib.parse import quote

import httpx

from aurora.config import settings
from aurora.network import gate
from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.tools.untrusted import one_line, shorten

logger = logging.getLogger(__name__)

WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/rest.php/v1/search/page"
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
ARXIV_URL = "https://export.arxiv.org/api/query"
ATOM = {"a": "http://www.w3.org/2005/Atom"}

_STOPWORDS = {
    "a", "an", "the", "of", "on", "in", "about", "and", "or", "for", "to",
    "with", "into", "from", "by", "at", "is", "are", "recent", "latest", "new",
}


class ServiceError(Exception):
    """A problem to explain to the user."""


def user_agent() -> str:
    """
    How AURORA identifies itself. Wikimedia requires contact details (an
    email address or URL) and refuses requests without them, so the
    contact from settings is included when the user has set one.
    """
    contact = (settings.RESEARCH_CONTACT or "").strip()
    details = "personal research assistant" + (f"; {contact}" if contact else "")
    return f"{settings.APP_NAME}/{settings.VERSION} ({details})"


NEEDS_CONTACT = (
    "Wikipedia only answers apps that include contact details (an email "
    "address or a web page) with each request. Set RESEARCH_CONTACT in "
    "aurora/config/settings.py to enable it. That contact is sent to Wikipedia "
    "with each lookup; nothing else about you is."
)


class _HttpTool(BaseTool):

    required_permissions = (Permission.NETWORK,)
    service = "the service"

    def __init__(self, name: str, client: httpx.Client | None = None):
        super().__init__(name)
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = gate.client(
                self.name,
                timeout=settings.RESEARCH_TIMEOUT_SECONDS,
                headers={"User-Agent": user_agent()},
                follow_redirects=True,
            )
        return self._client

    def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        try:
            response = self.client.get(url, params=params)
            if response.status_code == 404:
                return response
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise ServiceError(f"{self.service} took too long to respond.") from None
        except httpx.HTTPStatusError as error:
            raise ServiceError(
                f"{self.service} returned an error ({error.response.status_code})."
            ) from None
        except httpx.HTTPError:
            raise ServiceError(f"I couldn't reach {self.service}.") from None


class WikipediaTool(_HttpTool):

    service = "Wikipedia"

    def __init__(self, client: httpx.Client | None = None):
        super().__init__("Wikipedia", client)

    def _json(self, url, params=None) -> dict:
        response = self._get(url, params)
        if response.status_code == 404:
            return {}
        try:
            return response.json()
        except ValueError:
            raise ServiceError("Wikipedia sent a response I couldn't read.") from None

    def execute(self, topic: str = "", **_) -> ToolResult:
        topic = one_line(topic)
        if not topic:
            return ToolResult(False, "What should I look up?")

        # Checked only for the real client; tests inject their own.
        if self._client is None and not (settings.RESEARCH_CONTACT or "").strip():
            return ToolResult(False, NEEDS_CONTACT)

        try:
            pages = self._json(WIKIPEDIA_SEARCH_URL, {"q": topic, "limit": 3}).get("pages") or []
            if not pages:
                return ToolResult(False, f"Wikipedia has no article matching \"{topic}\".")

            key = pages[0]["key"]
            summary = self._json(WIKIPEDIA_SUMMARY_URL + quote(key, safe=""))
        except ServiceError as error:
            logger.info("Wikipedia lookup failed: %s", error)
            return ToolResult(False, f"{error} I won't guess the answer.")
        except (KeyError, TypeError, IndexError):
            logger.exception("Unexpected Wikipedia response")
            return ToolResult(False, "Wikipedia sent data I didn't expect. I won't guess the answer.")

        title = one_line(summary.get("title") or pages[0].get("title"))

        if summary.get("type") == "disambiguation":
            options = [one_line(p.get("title")) for p in pages if p.get("key") != key]
            hint = f" For example: {', '.join(options)}." if options else ""
            return ToolResult(
                False,
                f"\"{title}\" can mean several things on Wikipedia. Could you be more specific?{hint}",
                summary,
            )

        extract = shorten(summary.get("extract") or "", settings.RESEARCH_MAX_CHARS)
        if not extract:
            return ToolResult(False, f"Wikipedia's article on {title} has no summary.")

        url = (summary.get("content_urls") or {}).get("desktop", {}).get("page", "")
        source = f"Source: Wikipedia, \"{title}\"" + (f" ({url})" if url else "")
        return ToolResult(True, f"{title}: {extract}\n{source}", summary)


class ArxivTool(_HttpTool):

    service = "arXiv"

    def __init__(self, client: httpx.Client | None = None):
        super().__init__("arXiv", client)

    @staticmethod
    def build_query(topic: str) -> str:
        words = [w for w in re.findall(r"[\w-]+", topic.lower()) if w not in _STOPWORDS]
        return " AND ".join(f"all:{w}" for w in words[:8])

    def execute(self, topic: str = "", recent: bool = False, **_) -> ToolResult:
        topic = one_line(topic)
        query = self.build_query(topic)
        if not query:
            return ToolResult(False, "What topic should I find papers on?")

        try:
            response = self._get(ARXIV_URL, {
                "search_query": query,
                "start": 0,
                "max_results": settings.ARXIV_MAX_RESULTS,
                "sortBy": "submittedDate" if recent else "relevance",
                "sortOrder": "descending",
            })
            feed = ElementTree.fromstring(response.content)
        except ServiceError as error:
            logger.info("arXiv lookup failed: %s", error)
            return ToolResult(False, f"{error} I won't guess what the research says.")
        except ElementTree.ParseError:
            logger.exception("Unreadable arXiv response")
            return ToolResult(False, "arXiv sent a response I couldn't read. I won't guess what the research says.")

        papers = []
        for entry in feed.findall("a:entry", ATOM):
            title = one_line(entry.findtext("a:title", "", ATOM))
            link = one_line(entry.findtext("a:id", "", ATOM))
            if not title or not link.startswith("http"):
                continue
            authors = [one_line(a.findtext("a:name", "", ATOM)) for a in entry.findall("a:author", ATOM)]
            papers.append({
                "title": title,
                "authors": [a for a in authors if a],
                "published": one_line(entry.findtext("a:published", "", ATOM))[:10],
                "url": link,
                "summary": shorten(entry.findtext("a:summary", "", ATOM), 300),
            })

        if not papers:
            return ToolResult(True, f"I found no arXiv papers matching \"{topic}\".", [])

        lines = []
        for number, paper in enumerate(papers, start=1):
            authors = ", ".join(paper["authors"][:3]) + (" et al." if len(paper["authors"]) > 3 else "")
            lines.append(f"{number}. {paper['title']} ({paper['published']})\n   {authors}\n   {paper['url']}")

        order = "Newest" if recent else "Most relevant"
        message = (
            f"{order} arXiv papers on {topic}:\n" + "\n".join(lines) +
            "\nSource: arXiv. These are preprints; not all have been peer-reviewed."
        )
        return ToolResult(True, message, papers)
