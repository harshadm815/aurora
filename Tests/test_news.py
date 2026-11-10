"""Universal News Intelligence: queries, sources, checks, reports, tool, page.

No test uses the network: every source gets canned responses.
"""

from datetime import date, datetime, timedelta

import httpx
import pytest

from aurora.config import settings
from aurora.kernel.permissions import PermissionManager
from aurora.network import NetworkBlocked, NetworkGate
from aurora.news import processing
from aurora.news.article import FEED, GDELT, GOOGLE_NEWS, Article, Story
from aurora.news.query import COMPARE, INVESTIGATE, SEARCH, NewsQuery, is_news_request, parse
from aurora.news.report import investigation_text, report_text, search_text
from aurora.news.saved import SavedArticles
from aurora.news.service import NewsService
from aurora.news.sources import (
    FeedSource, GdeltSource, GoogleNewsSource, SourceResult, WebSearchSource, parse_feed,
)
from aurora.routing.intent import Intent
from aurora.routing.router import IntentRouter
from aurora.tool_plugins.news import BACKGROUND_REFUSAL, NewsTool
from aurora.tools.executor import ToolExecutor
from aurora.tools.tool_manager import ToolManager

TODAY = date(2026, 11, 10)
NOW = datetime(2026, 11, 10, 18, 0)


def at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 11, day, hour)


def article(title, publisher, url, day=9, summary="", method=FEED, **extra) -> Article:
    return Article(title=title, publisher=publisher, url=url, method=method, published=at(day), summary=summary,
                   **extra)


class FakeSource:
    def __init__(self, name, articles=(), status="ok", note="", blocked=None):
        self.name, self.articles, self.status, self.note, self.blocked = name, list(articles), status, note, blocked
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return SourceResult(self.name, self.status, [Article(**vars(a)) for a in self.articles], self.note,
                            self.blocked)


def service(*sources):
    return NewsService(feeds=[], sources=sources, now=lambda: NOW)


def query(**fields) -> NewsQuery:
    fields.setdefault("since", TODAY - timedelta(days=7))
    fields.setdefault("until", TODAY)
    return NewsQuery(**fields)


# --- Understanding requests -------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Find today's major achievements around the world.",
     dict(topic="", place="", since=TODAY, until=TODAY, category="achievements", mode=SEARCH)),
    ("What are the latest reported murder cases in India?",
     dict(topic="murder cases", place="India", category="crime and law")),
    ("Show me major scientific discoveries this month.",
     dict(topic="", since=date(2026, 11, 1), until=TODAY, category="science and space")),
    ("Compare today's international news from five publishers.",
     dict(mode=COMPARE, compare_count=5, since=TODAY, topic="")),
    ("Find recent developments in artificial intelligence.",
     dict(topic="artificial intelligence", since=TODAY - timedelta(days=7))),
    ("news about artificial intelligence from BBC and The Hindu, not from NPR",
     dict(topic="artificial intelligence", publishers=("BBC", "Hindu"), exclude=("NPR",))),
    ("investigate the story of the Chandrayaan landing since August 2026",
     dict(topic="Chandrayaan landing", mode=INVESTIGATE, since=date(2026, 8, 1))),
    ("crime news in Mumbai last 3 days", dict(place="Mumbai", since=date(2026, 11, 7), category="crime and law")),
    ("sports news in June 2026", dict(since=date(2026, 6, 1), until=date(2026, 6, 30), category="sports")),
])
def test_requests_are_understood(text, expected):
    parsed = parse(text, TODAY, settings.NEWS_FEEDS)
    for name, value in expected.items():
        assert getattr(parsed, name) == value, name


def test_only_topic_and_place_become_search_terms():
    parsed = parse("What are the latest reported murder cases in India?", TODAY)
    assert parsed.terms() == "murder cases India"
    assert parse("Find today's major achievements around the world.", TODAY).terms() == \
        "(award OR record OR prize OR milestone)"


def test_investigations_default_to_three_months():
    parsed = parse("investigate the Pune bridge collapse story", TODAY)
    assert parsed.mode == INVESTIGATE and parsed.since == TODAY - timedelta(days=90)


@pytest.mark.parametrize("text", ["tell me about black holes", "recent papers on dark matter", "what's the weather",
                                  "remember that my sister likes news", "hello", "summarize my notes on climate"])
def test_other_requests_are_not_news(text):
    assert not is_news_request(text)


@pytest.mark.parametrize("text", ["Find today's major achievements around the world.",
                                  "What are the latest reported murder cases in India?",
                                  "Find the latest developments in the investigation of the Pune bridge collapse",
                                  "Compare today's international news from five publishers.",
                                  "Find recent developments in artificial intelligence.", "any news?"])
def test_news_requests_reach_the_news_tool(text):
    decision = IntentRouter().route(text)
    assert decision.intent is Intent.NEWS and decision.arguments["request"]


@pytest.mark.parametrize("text", ["every morning show me the news", "every day at 7am give me the headlines",
                                  "monitor news about ISRO", "keep me updated on the Pune case news"])
def test_background_news_is_refused(text):
    decision = IntentRouter().route(text)
    assert decision.intent is Intent.NEWS and decision.arguments.get("background")
    result = NewsTool(service()).execute(**decision.arguments)
    assert not result.success and result.message == BACKGROUND_REFUSAL


def test_reminders_about_news_are_still_reminders():
    decision = IntentRouter().route("every evening at 9 remind me to read the news")
    assert decision.intent is Intent.SCHEDULE and decision.arguments["kind"] == "reminder"


# --- Sources -------------------------------------------------------------------------

def mock(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = []
    original = client.send

    def send(request, **kwargs):
        client.seen.append(request)
        return original(request, **kwargs)

    client.send = send
    return client


GDELT_JSON = {"articles": [
    {"url": "https://www.example-news.in/city/arrest-made?utm_source=x", "title": "Police arrest suspect in Pune case",
     "seendate": "20261109T063000Z", "domain": "example-news.in", "language": "English", "sourcecountry": "India"},
    {"url": "javascript:alert(1)", "title": "bad link", "seendate": "20261109T063000Z"},
]}


def test_gdelt_articles_and_parameters():
    client = mock(lambda r: httpx.Response(200, json=GDELT_JSON))
    result = GdeltSource(client, clock=lambda: 1000.0, sleep=lambda s: None, today=TODAY).search(
        query(topic="Pune case AI"))

    assert result.status == "ok" and len(result.articles) == 1
    first = result.articles[0]
    assert first.method == GDELT and first.domain == "example-news.in" and first.country == "India"
    assert first.date_note == "time GDELT first saw it"
    params = dict(client.seen[0].url.params)
    assert params["query"] == 'Pune case "AI"'          # short words are quoted, as GDELT requires
    assert params["startdatetime"] == "20261103000000" and params["enddatetime"] == "20261111000000"


def test_gdelt_waits_five_seconds_between_requests():
    GdeltSource._last = 0.0
    clock = [100.0]
    waits = []
    source = GdeltSource(mock(lambda r: httpx.Response(200, json={"articles": []})), clock=lambda: clock[0],
                         sleep=waits.append, today=TODAY)
    source.search(query(topic="flood"))
    clock[0] += 2
    source.search(query(topic="flood"))
    assert waits == [pytest.approx(3.0)]


@pytest.mark.parametrize("response, note", [
    (httpx.Response(429, text="Please limit requests"), "limiting requests"),
    (httpx.Response(200, text="The specified phrase is too short."), "didn't accept the search"),
    (httpx.Response(500), "returned an error"),
])
def test_gdelt_problems_are_explained(response, note):
    GdeltSource._last = 0.0
    result = GdeltSource(mock(lambda r: response), clock=lambda: 1000.0, sleep=lambda s: None,
                         today=TODAY).search(query(topic="flood"))
    assert result.status == "failed" and note in result.note


def test_gdelt_says_what_it_cannot_cover():
    source = GdeltSource(mock(lambda r: pytest.fail("no request expected")), today=TODAY)
    assert "3 months" in source.search(query(topic="flood", since=date(2025, 1, 1), until=date(2025, 2, 1))).note
    assert source.search(query()).status == "skipped"          # no terms: GDELT has no top stories


GOOGLE_RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Rover finds water ice - The Hindu</title>
<link>https://news.google.com/rss/articles/abc?oc=5</link>
<pubDate>Mon, 09 Nov 2026 04:37:45 GMT</pubDate>
<source url="https://www.thehindu.com">The Hindu</source></item>
</channel></rss>"""


def test_google_news_items(monkeypatch):
    monkeypatch.setattr(settings, "NEWS_EDITION", "IN:en")
    client = mock(lambda r: httpx.Response(200, content=GOOGLE_RSS))
    result = GoogleNewsSource(client).search(query(topic="water ice"))

    [item] = result.articles
    assert item.title == "Rover finds water ice" and item.publisher == "The Hindu"
    assert item.domain == "thehindu.com" and not item.original_url_known
    q = dict(client.seen[0].url.params)["q"]
    assert q == "water ice after:2026-11-02 before:2026-11-11"


RSS = b"""<?xml version="1.0"?><rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item><title>Flood waters rise in Assam</title><link>https://feed.example/assam-flood</link>
<pubDate>Mon, 09 Nov 2026 08:00:00 +0530</pubDate><description>&lt;p&gt;Rescue teams were sent.&lt;/p&gt;</description>
<dc:creator>A. Writer</dc:creator></item>
<item><title>Cricket final tonight</title><link>https://feed.example/cricket</link>
<pubDate>Mon, 09 Nov 2026 08:00:00 +0530</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Telescope spots new comet</title><link rel="alternate" href="https://atom.example/comet"/>
<published>2026-11-08T10:00:00Z</published><summary>Astronomers report a comet.</summary>
<author><name>Sky Desk</name></author></entry></feed>"""


def test_rss_and_atom_feeds_are_read():
    rss = parse_feed(RSS, {"name": "Example", "url": "https://feed.example/rss", "country": "India"})
    assert [a.title for a in rss] == ["Flood waters rise in Assam", "Cricket final tonight"]
    assert rss[0].summary == "Rescue teams were sent." and rss[0].author == "A. Writer"
    atom = parse_feed(ATOM, {"name": "Sky", "url": "https://atom.example/feed", "official": True})
    assert atom[0].url == "https://atom.example/comet" and atom[0].official and atom[0].author == "Sky Desk"


def test_feeds_get_no_search_terms_and_are_matched_here():
    client = mock(lambda r: httpx.Response(200, content=RSS))
    feed = FeedSource({"name": "Example", "url": "https://feed.example/rss"}, client)
    report = service(feed).search(query(topic="flood"))
    assert str(client.seen[0].url) == "https://feed.example/rss"            # nothing about the search
    assert [a.title for a in report.articles] == ["Flood waters rise in Assam"]


def test_web_search_needs_a_key_and_sends_it_only_to_brave():
    assert "isn't set up" in WebSearchSource(key="").search(query(topic="x")).note
    payload = {"results": [{"title": "Quake hits", "url": "https://web.example/quake",
                            "description": "A strong quake.", "page_age": "2026-11-09T05:00:00Z",
                            "meta_url": {"hostname": "web.example"}}]}
    client = mock(lambda r: httpx.Response(200, json=payload))
    result = WebSearchSource(client, key="secret").search(query(topic="quake"))
    assert result.articles[0].publisher == "web.example"
    assert client.seen[0].headers["X-Subscription-Token"] == "secret"
    assert client.seen[0].url.host == "api.search.brave.com"


# --- Checks on articles -------------------------------------------------------------

@pytest.mark.parametrize("text, stage", [
    ("Police arrest man over Pune murder", "arrested"),
    ("Man charged with murder after Pune killing", "charged"),
    ("Court convicts man of 2024 murder, sentenced to life", "convicted"),
    ("Accused acquitted in murder case, court finds not guilty", "acquitted"),
    ("Police investigating death of shopkeeper", "under investigation"),
    ("Actor accused of fraud by former partner", "alleged"),
    ("Team wins cricket final", ""),
])
def test_legal_stages(text, stage):
    assert processing.legal_stage(text) == stage


def test_annotations():
    item = processing.annotate(article("Correction: Minister says 12 killed (PTI)", "Daily", "https://daily.example/x",
                                       summary="Officials said on Monday that the toll rose. PTI"), NOW)
    assert item.correction and item.agency == "PTI"
    assert item.event_date == datetime(2026, 11, 9)      # "on Monday", published Mon 9 Nov
    official = processing.annotate(article("Statement", "Ministry", "https://www.mohfw.gov.in/s"), NOW)
    assert official.official and official.domain == "mohfw.gov.in"


def test_future_dates_are_not_trusted():
    item = processing.annotate(article("x", "p", "https://p.example/x", day=20), NOW)
    assert item.published is None and "future" in item.date_note


def test_crime_summaries_leave_out_graphic_details():
    item = processing.annotate(article("Man arrested over killing", "Daily", "https://daily.example/k",
                                       summary="Police arrested a man on Monday. The body was found mutilated. "
                                               "He will appear in court."), NOW)
    assert item.summary == "Police arrested a man on Monday. He will appear in court."


def test_same_link_from_two_services_is_one_article():
    a = article("Flood in Assam", "Example", "https://www.example.in/flood?utm_source=gdelt", method=GDELT)
    b = article("Flood in Assam", "Example", "https://example.in/flood/", method=FEED, summary="Teams sent.")
    merged, duplicates = processing.merge_duplicates([a, b])
    assert duplicates == 1 and merged[0].also_via == [FEED] and merged[0].summary == "Teams sent."


def test_independent_reports_and_agency_copies():
    articles = [processing.annotate(a, NOW) for a in [
        article("Earthquake of magnitude 6.1 strikes northern Japan coast", "NHK", "https://nhk.example/q"),
        article("Magnitude 6.1 earthquake strikes northern Japan coast", "Daily One", "https://one.example/q",
                summary="TOKYO (Reuters) - A strong quake struck."),
        article("6.1 magnitude earthquake strikes northern Japan coast, says agency", "Daily Two",
                "https://two.example/q", summary="TOKYO (Reuters) - A strong quake struck."),
        article("Team wins cricket final in Mumbai", "Sports Daily", "https://sports.example/c"),
    ]]
    stories = processing.rank(processing.group_stories(articles))
    quake = stories[0]
    assert len(quake.articles) == 3
    assert quake.independent == ["NHK", "Reuters (via Daily One)"]       # one agency story counts once


def test_different_numbers_are_reported_as_differences():
    story = Story([processing.annotate(article("Bus crash: 12 killed in Himachal", "A", "https://a.example/b"), NOW),
                   processing.annotate(article("Himachal bus crash leaves 15 dead", "B", "https://b.example/b"), NOW)])
    assert processing.differences(story) == ["number killed: 12 (A) vs 15 (B)"]


# --- Reports -------------------------------------------------------------------------

def crime_service():
    return service(FakeSource("GDELT", [
        article("Police arrest suspect in Pune bakery owner murder", "City Times", "https://city.example/1", day=4),
        article("Pune bakery owner murder: suspect charged by police", "Pune Daily", "https://pune.example/2", day=7),
        article("Pune bakery owner murder suspect arrested, police say", "State News", "https://state.example/3", day=4),
    ], ), FakeSource("web search", status="skipped", note="web news search isn't set up"))


def test_crime_reports_never_present_guilt_as_established():
    report = crime_service().search(query(topic="Pune bakery murder"))
    text = search_text(report, 10)
    assert "Charges are reported. Not proven in court." in text
    assert "guilty" not in text.replace("found guilty", "")


def test_reports_say_what_was_and_wasnt_searched():
    report = crime_service().search(query(topic="Pune bakery murder"))
    text = report_text(report, 10)
    assert "Searched: GDELT (3)" in text
    assert "Not searched: web news search isn't set up" in text
    assert "not every report published" in text


def test_nothing_is_invented_when_nothing_is_found():
    report = service(FakeSource("GDELT", [])).search(query(topic="zzzz"))
    text = report_text(report, 10)
    assert "I found no reports matching that" in text and "won't fill the gap" in text


def test_dates_and_publisher_choices_are_applied():
    report = service(FakeSource("GDELT", [
        article("Old report on floods", "A", "https://a.example/old", day=1),
        article("New report on floods", "BBC News", "https://bbc.example/new", day=9),
        article("Another report on floods", "NPR", "https://npr.example/n", day=9),
    ])).search(query(topic="floods", since=date(2026, 11, 5), exclude=("NPR",)))
    assert [a.title for a in report.articles] == ["New report on floods"]
    assert report.out_of_range == 1


def test_investigation_timeline():
    report = crime_service().search(query(topic="Pune bakery murder", mode=INVESTIGATE))
    text = investigation_text(report)
    assert "Earliest report found: 04 Nov 2026" in text
    assert text.index("04 Nov 2026") < text.index("07 Nov 2026")
    assert "Legal process as reported (first report of each stage): arrested (04 Nov) -> charged (07 Nov)." in text
    assert "Not proven in court" in text


def test_comparing_publishers():
    report = service(FakeSource("Google News", [
        article("Leaders meet for climate summit in Antalya", "BBC", "https://g.example/1", method=GOOGLE_NEWS),
        article("Climate summit in Antalya: leaders meet", "Al Jazeera", "https://g.example/2", method=GOOGLE_NEWS),
        article("Rupee falls against dollar", "Mint", "https://g.example/3", method=GOOGLE_NEWS),
    ])).search(query(mode=COMPARE, compare_count=5))
    text = report_text(report, 10)
    assert "Publishers compared:" in text
    assert 'BBC: "Leaders meet for climate summit in Antalya"' in text
    assert 'Only Mint: "Rupee falls against dollar"' in text


# --- The tool, the gate and the chat ----------------------------------------------------

def test_tool_reports_through_the_chat():
    tool = NewsTool(crime_service())
    result = tool.execute(request="latest news about the Pune bakery murder")
    assert result.success and "Pune bakery owner murder" in result.message
    assert result.sources and result.sources[0].kind == "live"


def test_going_online_is_asked_for_first(monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "ask")
    gate = NetworkGate()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=GOOGLE_RSS)

    client = gate.client("News", transport=httpx.MockTransport(handler))
    tool = NewsTool(NewsService(feeds=[], sources=[GoogleNewsSource(client)]))
    tools = ToolManager(PermissionManager({"News": ["network"]}))
    tools.register(tool)
    executor = ToolExecutor(tools, network=gate)
    decision = IntentRouter().route("latest news about water ice")

    pending = executor.execute(decision)
    assert pending.needs_confirmation and calls == []
    assert "only your search terms" in pending.message and "news.google.com" in pending.message
    result = executor.confirm("News", pending)
    assert result.success and "Rover finds water ice" in result.message
    assert len(calls) == 1 and dict(calls[0].url.params)["q"].startswith("water ice")


def test_scheduled_routines_never_search_the_news(memory):
    from aurora.automation.schedule import Task
    from aurora.brain.brain import Brain
    from aurora.kernel.engine import AuroraEngine
    from conftest import FakeProvider

    engine = AuroraEngine(Brain(FakeProvider()), memory=memory)
    engine.routines.save("morning update", "latest news about water ice")
    task = Task(1, "run your routine", "routine", {"routine": "morning update"}, "daily", "07:00", NOW)
    result = engine.run_task(task)
    assert not result.success and "only when you ask" in result.message
    engine.shutdown()


# --- Saved articles ----------------------------------------------------------------------

def test_saved_articles(tmp_path):
    saved = SavedArticles(tmp_path / "news.db")
    item = article("Flood in Assam", "Example", "https://example.in/flood?utm_source=x")
    assert saved.save(item, "Floods")
    assert not saved.save(item, "Assam")                 # already saved: moved instead
    [entry] = saved.all()
    assert entry.folder == "Assam" and entry.url == "https://example.in/flood"
    saved.move(entry.id, "Archive")
    assert saved.folders() == ["Archive"]
    assert saved.delete(entry.id) and saved.all() == []


# --- News Centre ------------------------------------------------------------------------

@pytest.fixture
def news_page(qapp_news, tmp_path, monkeypatch):
    from aurora.interface.news_view import NewsView

    from PySide6.QtCore import QObject, Signal

    class Backend(QObject):
        engine_ready = Signal()
        engine = None

        def run_io(self, function, on_result=None, on_error=None):
            try:
                value = function()
            except Exception as error:
                on_error(error)
            else:
                on_result(value)

    view = NewsView(Backend())
    yield view
    from conftest import release_widgets
    release_widgets()


@pytest.fixture(scope="module")
def qapp_news():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_news_centre_shows_cards_and_saves(news_page):
    from aurora.news.query import NewsQuery
    from aurora.tools.result import ToolResult
    from PySide6.QtCore import QUrl

    report = crime_service().search(query(topic="Pune bakery murder"))
    news_page._show(ToolResult(True, "", report))
    html = news_page.results.toHtml()
    assert "Pune bakery owner murder" in html and "Not proven in court" in html
    assert "Not searched" in news_page.status.text()

    news_page._clicked(QUrl("save:0"))
    assert [a.title for a in news_page.saved.all()] == [report.stories[0].lead.title]
    assert news_page.saved_list.count() == 1


def test_news_centre_filters_build_the_query(news_page):
    news_page.search_box.setText("floods")
    news_page.place.setText("Assam")
    news_page.only.setText("BBC, The Hindu")
    news_page.period.setCurrentIndex(3)          # past month
    built = news_page.build_query(COMPARE)
    assert (built.topic, built.place, built.publishers, built.mode) == ("floods", "Assam", ("BBC", "The Hindu"), COMPARE)
    assert built.until - built.since == timedelta(days=30)
