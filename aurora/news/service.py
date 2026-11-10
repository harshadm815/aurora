"""
Runs a news search or investigation: every enabled source in parallel,
then processing, then a NewsReport that says exactly what was searched
and what wasn't.

Only called when you ask (the News tool and the News Centre). Nothing in
AURORA calls it on a timer.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

from aurora.config import settings
from aurora.network import NetworkBlocked
from aurora.news import processing
from aurora.news.article import FEED, Article, Story
from aurora.news.query import COMPARE, INVESTIGATE, NewsQuery
from aurora.news.sources import (
    FeedSource, GdeltSource, GoogleNewsSource, SourceResult, WebSearchSource, in_range, register_feed_hosts,
)

logger = logging.getLogger(__name__)


@dataclass
class NewsReport:
    query: NewsQuery
    stories: list[Story]
    results: list[SourceResult]
    articles_found: int = 0          # before filtering
    duplicates: int = 0
    out_of_range: int = 0            # outside the dates, or no date
    created: datetime = field(default_factory=datetime.now)

    @property
    def searched(self) -> list[SourceResult]:
        return [r for r in self.results if r.status == "ok"]

    @property
    def problems(self) -> list[SourceResult]:
        return [r for r in self.results if r.status in ("failed", "blocked")]

    @property
    def skipped(self) -> list[SourceResult]:
        return [r for r in self.results if r.status == "skipped"]

    @property
    def blocked(self) -> NetworkBlocked | None:
        """Set when nothing could be searched because going online needs your OK."""
        if self.searched:
            return None
        for result in self.results:
            if result.blocked is not None and result.blocked.can_ask:
                return result.blocked
        return None

    @property
    def articles(self) -> list[Article]:
        return [a for s in self.stories for a in s.articles]


class NewsService:

    def __init__(self, feeds=None, sources=None, now=None):
        self.feeds = list(settings.NEWS_FEEDS if feeds is None else feeds)
        self._sources = sources                    # injectable in tests
        self.now = now or datetime.now
        register_feed_hosts(self.feeds)

    def sources_for(self, query: NewsQuery) -> list:
        if self._sources is not None:
            return list(self._sources)
        sources = []
        if settings.NEWS_USE_GDELT:
            sources.append(GdeltSource())
        if settings.NEWS_USE_GOOGLE_NEWS:
            sources.append(GoogleNewsSource())
        sources.append(WebSearchSource())
        if settings.NEWS_USE_FEEDS:
            feeds = self.feeds
            if query.publishers:              # only the publishers you named, if they have a feed here
                feeds = [f for f in feeds if processing._matches_publisher(
                    Article(f.get("name", ""), f.get("name", ""), f.get("url", ""), FEED), query.publishers)]
            if query.exclude:
                feeds = [f for f in feeds if not processing._matches_publisher(
                    Article(f.get("name", ""), f.get("name", ""), f.get("url", ""), FEED), query.exclude)]
            sources += [FeedSource(feed) for feed in feeds]
        return sources

    def search(self, query: NewsQuery) -> NewsReport:
        sources = self.sources_for(query)
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(sources))),
                                thread_name_prefix="aurora-news") as pool:
            results = list(pool.map(lambda source: source.search(query), sources))
        logger.info("News search: %d sources, %s", len(results),
                    ", ".join(f"{r.source}={r.status}" for r in results))

        now = self.now()
        raw: list[Article] = []
        for result in results:
            articles = [processing.annotate(a, now) for a in result.articles]
            if result.source.endswith("(feed)"):
                articles = [a for a in articles if processing.matches(a, query)]
            result.articles = articles
            raw += articles
        found = len(raw)
        chosen = processing.select(raw, query)
        dated = [a for a in chosen if in_range(a, query.since, query.until)]
        out_of_range = len(chosen) - len(dated)
        merged, duplicates = processing.merge_duplicates(dated)
        stories = processing.rank(processing.group_stories(merged))
        if query.mode == INVESTIGATE:
            stories.sort(key=lambda s: s.earliest or datetime.max)
        return NewsReport(query, stories, results, found, duplicates, out_of_range)


# --- Comparing publishers -------------------------------------------------------

def compare(report: NewsReport) -> tuple[list[str], list[Story], dict[str, list[Story]]]:
    """The publishers with the most stories, stories two or more of them covered, and each one's own."""
    count = report.query.compare_count or 5
    tally: dict[str, int] = {}
    for story in report.stories:
        for publisher in story.publishers:
            tally[publisher] = tally.get(publisher, 0) + 1
    publishers = sorted(tally, key=lambda p: (-tally[p], p))[:count]
    shared = [s for s in report.stories if len(set(s.publishers) & set(publishers)) >= 2]
    only = {p: [s for s in report.stories if s.publishers == [p]][:3] for p in publishers}
    return publishers, shared, only


# --- Investigation ------------------------------------------------------------------

@dataclass
class Investigation:
    report: NewsReport
    earliest: Article | None
    timeline: list[tuple[date, Story, Article]]   # each day a story had new reports, and its first report that day
    official: list[Article]
    corrections: list[Article]
    legal: list[tuple[str, date, Article]]     # each stage, when it was first reported, by whom
    differences: list[str]


def investigate(report: NewsReport) -> Investigation:
    dated = [a for a in report.articles if a.published]
    earliest = min(dated, key=lambda a: a.published) if dated else None
    timeline = []
    for story in report.stories:
        days: dict[date, Article] = {}
        for item in sorted((a for a in story.articles if a.published), key=lambda a: a.published):
            days.setdefault(item.published.date(), item)
        timeline += [(day, story, item) for day, item in days.items()]
    timeline.sort(key=lambda entry: (entry[0], entry[2].published))
    official = sorted((a for a in report.articles if a.official), key=lambda a: a.published or datetime.max)
    corrections = [a for a in report.articles if a.correction]
    first: dict[str, tuple[date, Article]] = {}
    for article in sorted(dated, key=lambda a: a.published):
        if article.legal_stage and article.legal_stage not in first:
            first[article.legal_stage] = (article.published.date(), article)
    from aurora.news.article import LEGAL_STAGES
    legal = [(stage, first[stage][0], first[stage][1]) for stage in LEGAL_STAGES if stage in first]
    differences = [f"{s.lead.title[:60]}: {d}" for s in report.stories for d in s.differences]
    return Investigation(report, earliest, timeline, official, corrections, legal, differences)


def is_compare(query: NewsQuery) -> bool:
    return query.mode == COMPARE
