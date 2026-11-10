"""
The News tool: news searches, publisher comparisons and investigations,
only when you ask.

    find today's major achievements around the world
    what are the latest reported murder cases in India?
    compare today's international news from five publishers
    investigate the Pune bridge collapse story

Only the topic and place are sent (to GDELT, Google News and, if set up,
web search); publisher feeds are downloaded whole and searched here. If
going online needs your OK, the request is held and the chat asks you.
"""

import logging

from aurora.config import settings
from aurora.kernel.permissions import Permission
from aurora.knowledge.sources import LIVE, Source
from aurora.news.query import NewsQuery, parse
from aurora.news.report import report_text
from aurora.news.service import NewsReport, NewsService
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

logger = logging.getLogger(__name__)

BACKGROUND_REFUSAL = (
    "I only search the news when you ask, so I don't monitor it, run scheduled news searches or send news "
    "alerts. Ask me whenever you want an update, for example \"latest news about the Pune bridge collapse\"."
)


class NewsTool(BaseTool):

    required_permissions = (Permission.NETWORK,)

    def __init__(self, service: NewsService | None = None):
        super().__init__("News")
        self._service = service
        self.last_report: NewsReport | None = None      # for the News Centre

    @property
    def service(self) -> NewsService:
        if self._service is None:
            self._service = NewsService()
        return self._service

    def run(self, query: NewsQuery) -> NewsReport:
        """A search for the News Centre (raises NetworkBlocked if going online needs your OK)."""
        report = self.service.search(query)
        if report.blocked is not None:
            raise report.blocked
        self.last_report = report
        return report

    def execute(self, request: str = "", query: NewsQuery | None = None, background: bool = False,
                **_) -> ToolResult:
        if background:
            return ToolResult(False, BACKGROUND_REFUSAL)
        if query is None:
            if not request.strip():
                return ToolResult(False, "What news should I look for?")
            query = parse(request, feeds=self.service.feeds)
        report = self.run(query)                 # a block goes to the executor, which asks you
        if not report.searched:
            return ToolResult(False, report_text(report, settings.NEWS_MAX_STORIES), report, retryable=True)
        sources = [Source(LIVE, a.publisher, a.url, a.published) for s in report.stories[:5] for a in s.articles[:1]]
        return ToolResult(True, report_text(report, settings.NEWS_MAX_STORIES), report, sources=sources)
