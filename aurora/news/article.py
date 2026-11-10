"""What AURORA knows about one news article, and about one story (the same
event reported by several publishers)."""

from dataclasses import dataclass, field
from datetime import datetime

# Retrieval methods, as shown in reports.
GDELT = "GDELT"
GOOGLE_NEWS = "Google News"
FEED = "publisher feed"
WEB = "web search"

# What a crime report says about the legal process, in order. A later stage
# is only shown as reported; it never decides guilt by itself.
LEGAL_STAGES = ("under investigation", "alleged", "arrested", "charged", "on trial", "convicted", "acquitted")


@dataclass
class Article:
    title: str
    publisher: str
    url: str
    method: str                              # GDELT, GOOGLE_NEWS, FEED or WEB
    published: datetime | None = None        # None: the source gave no date
    date_note: str = ""                      # "date as first seen by GDELT", "date looks wrong"…
    summary: str = ""
    author: str = ""
    domain: str = ""                         # publisher's site (from the URL or the source)
    original_url_known: bool = True          # False for Google News redirect links
    country: str = ""                        # the publisher's country, when the source says
    official: bool = False                   # an official body, court, agency or journal
    language: str = ""

    # Filled in by processing
    category: str = ""
    event_date: datetime | None = None
    legal_stage: str = ""                    # one of LEGAL_STAGES, for crime and court reports
    allegation: bool = False                 # words like "alleged", "accused", "reportedly"
    correction: bool = False                 # says it was corrected or updated
    agency: str = ""                         # "PTI", "Reuters"… when credited (syndicated copy)
    also_via: list[str] = field(default_factory=list)   # other methods that returned the same URL

    def key(self) -> str:
        return self.url


@dataclass
class Story:
    """Articles about the same event, newest first."""

    articles: list[Article]
    differences: list[str] = field(default_factory=list)   # "reported deaths: 12 / 15"

    @property
    def lead(self) -> Article:
        """The article shown first: one with a known original link, an official source, then the newest."""
        return sorted(self.articles, key=lambda a: (not a.original_url_known, not a.official,
                                                    -(a.published.timestamp() if a.published else 0)))[0]

    @property
    def publishers(self) -> list[str]:
        seen = []
        for article in self.articles:
            if article.publisher and article.publisher not in seen:
                seen.append(article.publisher)
        return seen

    @property
    def independent(self) -> list[str]:
        """Publishers that reported it themselves: copies of one agency's story count once."""
        names, agencies = [], set()
        for article in self.articles:
            if article.agency:
                if article.agency in agencies:
                    continue
                agencies.add(article.agency)
                label = f"{article.agency} (via {article.publisher})"
            else:
                label = article.publisher
            if label and label not in names and article.publisher not in names:
                names.append(label)
        return names

    @property
    def official(self) -> list[Article]:
        return [a for a in self.articles if a.official]

    @property
    def earliest(self) -> datetime | None:
        dates = [a.published for a in self.articles if a.published]
        return min(dates) if dates else None

    @property
    def latest(self) -> datetime | None:
        dates = [a.published for a in self.articles if a.published]
        return max(dates) if dates else None

    @property
    def legal_stage(self) -> str:
        """The furthest stage any article reports (acquittal and conviction are final)."""
        stages = [a.legal_stage for a in self.articles if a.legal_stage]
        return max(stages, key=LEGAL_STAGES.index) if stages else ""

    @property
    def allegation(self) -> bool:
        return any(a.allegation for a in self.articles)

    @property
    def corrected(self) -> bool:
        return any(a.correction for a in self.articles)

    @property
    def category(self) -> str:
        counts: dict[str, int] = {}
        for article in self.articles:
            if article.category:
                counts[article.category] = counts.get(article.category, 0) + 1
        return max(counts, key=counts.get) if counts else ""
