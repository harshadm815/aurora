"""
The AURORA Browser: addresses, history and bookmarks, reader mode, page
answers, saving to Knowledge, the network gate, and the Browser page with
a fake web engine. Nothing here starts Chromium or uses the network.
"""

from datetime import datetime, timedelta

import pytest

from aurora.browser import addresses, answers, reader, saving
from aurora.browser.network import BrowserNetwork, is_other_site
from aurora.browser.store import BrowserStore
from aurora.config import settings
from aurora.network import NetworkGate
from aurora.network.gate import INTERNET, LOCAL, THIS_PC

ARTICLE = """<!doctype html><html><head><title>How black holes form | Space Site</title>
<meta name="description" content="A short guide to black hole formation.">
<script>var tracking = "black hole black hole black hole";</script><style>p{color:red}</style></head>
<body>
<header class="masthead"><a href="/">Space Site</a></header>
<nav><ul><li><a href="/news">News</a></li><li><a href="/science">Science</a></li></ul></nav>
<div class="cookie-banner"><p>We use cookies to give you the best experience on our website.</p></div>
<main>
<h1>How black holes form</h1>
<p class="byline">By A. Writer</p>
<p>A black hole forms when a massive star, at least twenty times the mass of the Sun, runs out of fuel
and its core collapses under its own gravity.</p>
<p>The collapse squeezes the core into a region so dense that not even light can escape from it.</p>
<h2>Sizes</h2>
<ul><li>Stellar black holes weigh between 5 and 100 solar masses.</li>
<li>Supermassive black holes weigh millions or billions of solar masses.</li></ul>
<div class="share-tools"><p>Share this article on social media with your friends today.</p></div>
<p>Astronomers first photographed a black hole in 2019 with the Event Horizon Telescope, an array of
radio telescopes spread across the world.</p>
<p>Read more: <a href="/a">Neutron stars explained in full detail for everyone</a></p>
<h2>Related</h2>
</main>
<aside><p>Most popular: ten amazing facts about Jupiter you never knew about.</p></aside>
<footer><p>Copyright 2026 Space Site. All rights reserved worldwide.</p></footer>
</body></html>"""


# --- Addresses ----------------------------------------------------------------------------

@pytest.mark.parametrize("typed, expected", [
    ("bbc.co.uk", "https://bbc.co.uk"),
    ("arxiv.org/abs/2401.00001", "https://arxiv.org/abs/2401.00001"),
    ("https://example.org/a?b=1#c", "https://example.org/a?b=1#c"),
    ("HTTP://Example.org", "http://Example.org"),
    ("localhost:8000", "http://localhost:8000"),
    ("192.168.1.40/readings", "http://192.168.1.40/readings"),
    ("8.8.8.8", "https://8.8.8.8"),
    ("black hole formation", "https://duckduckgo.com/?q=black+hole+formation"),
    ("what is 3.5 kg in pounds", "https://duckduckgo.com/?q=what+is+3.5+kg+in+pounds"),
    ("c++ & rust", "https://duckduckgo.com/?q=c%2B%2B+%26+rust"),
    ("", "about:blank"),
])
def test_address_bar_opens_addresses_and_searches_the_rest(typed, expected):
    assert addresses.resolve(typed) == expected


@pytest.mark.parametrize("typed", ["javascript:alert(1)", "file:///C:/Users/x/secret.txt", "data:text/html,<b>x</b>",
                                   "chrome://settings", "ftp://example.org/file", "vbscript:msgbox"])
def test_address_bar_refuses_anything_but_web_pages(typed):
    with pytest.raises(addresses.RefusedAddress):
        addresses.resolve(typed)


def test_search_engine_is_configurable(monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_SEARCH_URL", "https://search.example/?s={query}")
    assert addresses.resolve("dark matter") == "https://search.example/?s=dark+matter"


def test_address_helpers():
    assert addresses.host_of("https://News.BBC.co.uk/x") == "news.bbc.co.uk"
    assert addresses.is_web("https://a.org") and not addresses.is_web("about:blank")
    assert addresses.without_fragment("https://a.org/p?q=1#part") == "https://a.org/p?q=1"


# --- History and bookmarks ----------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 11, 10, 0)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    return BrowserStore(clock=clock)


def test_history_records_web_pages_once_per_visit(store, clock):
    assert store.record_visit("https://a.org/page#one", "")
    assert store.record_visit("https://a.org/page#two", "Page A")      # same page, title arrives
    clock.now += timedelta(minutes=5)
    assert store.record_visit("https://b.org/", "Page B")
    assert not store.record_visit("about:blank", "Start")
    assert not store.record_visit("file:///C:/x.txt", "x")
    visits = store.history()
    assert [(v.url, v.title) for v in visits] == [("https://b.org/", "Page B"), ("https://a.org/page", "Page A")]


def test_history_off_records_nothing(store, monkeypatch):
    monkeypatch.setattr(settings, "BROWSER_HISTORY", False)
    assert not store.record_visit("https://a.org/", "A")
    assert store.history() == []


def test_history_search_delete_clear_and_prune(store, clock):
    store.record_visit("https://a.org/", "Dark matter explained")
    clock.now += timedelta(hours=1)
    store.record_visit("https://b.org/100%_real", "Other")
    assert [v.title for v in store.history("dark")] == ["Dark matter explained"]
    assert [v.title for v in store.history("100%")] == ["Other"]          # % is literal, not a wildcard
    store.delete_visit(store.history("dark")[0].id)
    assert [v.title for v in store.history()] == ["Other"]
    clock.now += timedelta(days=settings.BROWSER_HISTORY_DAYS + 1)
    assert store.prune() == 1
    store.record_visit("https://c.org/", "C")
    assert store.clear_history() == 1 and store.history() == []


def test_bookmarks(store):
    assert store.add_bookmark("https://arxiv.org/#top", "arXiv")
    assert not store.add_bookmark("https://arxiv.org/", "arXiv papers", "Science")   # updated, not duplicated
    assert [(b.url, b.title, b.folder) for b in store.bookmarks()] == [("https://arxiv.org/", "arXiv papers", "Science")]
    assert store.bookmark_for("https://arxiv.org/#x") is not None
    with pytest.raises(ValueError):
        store.add_bookmark("javascript:alert(1)")
    store.record_visit("https://arxiv.org/list/astro-ph", "Astrophysics listing")
    assert store.suggestions("arx")[0] == ("https://arxiv.org/", "arXiv papers")
    assert ("https://arxiv.org/list/astro-ph", "Astrophysics listing") in store.suggestions("astro")
    store.remove_bookmark(store.bookmarks()[0].id)
    assert store.bookmarks() == []


# --- Reader mode --------------------------------------------------------------------------

def test_reader_keeps_the_article_and_drops_menus_adverts_and_scripts():
    page = reader.extract(ARTICLE, "https://space.example/bh")
    text = page.text
    assert page.title == "How black holes form | Space Site"
    assert page.description == "A short guide to black hole formation."
    assert "massive star" in text and "Event Horizon Telescope" in text and "Supermassive" in text
    for noise in ("cookies", "Share this", "Most popular", "Copyright", "tracking", "Science", "Neutron stars"):
        assert noise not in text, noise
    assert page.outline == ["How black holes form", "Sizes"]            # the empty "Related" heading is dropped
    markdown = page.markdown()
    assert "## How black holes form" in markdown and "- Stellar black holes" in markdown


def test_reader_without_a_marked_article_and_with_broken_html():
    page = reader.extract("<div><p>First real paragraph of text here.<div>Second block with enough words "
                          "in it.</div></p><p>Unclosed paragraph with several words<span>more", "https://x.org")
    assert "First real paragraph" in page.text and "Second block" in page.text and "Unclosed" in page.text
    assert page.title == "https://x.org"
    assert reader.extract("", "https://x.org").blocks == []


def test_reader_skips_hidden_text_and_unescapes_entities():
    page = reader.extract('<p hidden>Hidden text that should never show.</p>'
                          '<p style="display: none">Also hidden text here today.</p>'
                          '<p>Caf&eacute; prices rose by 5&nbsp;% this year.</p>', "https://x.org")
    assert page.text == "Café prices rose by 5 % this year."


# --- Answers about the open page ----------------------------------------------------------

def test_answers_quote_the_page():
    page = reader.extract(ARTICLE, "https://space.example/bh")
    result = answers.answer(page, "what happens when a massive star runs out of fuel?",
                            read=datetime(2026, 11, 11, 12, 0))
    assert result.status == "found"
    assert "twenty times the mass of the Sun" in result.quotes[0]
    text = result.text()
    assert "From this page:" in text and "https://space.example/bh" in text and "retrieved 11 Nov 2026" in text
    partial = answers.answer(page, "how massive must a star be to become a black hole?")
    assert partial.status == "partial" and "closest sentences" in partial.text()
    when = answers.answer(page, "when was a black hole first photographed?")
    assert when.status == "found" and "2019" in when.quotes[0]


def test_answers_say_when_the_page_doesnt_answer():
    page = reader.extract(ARTICLE, "https://space.example/bh")
    assert answers.answer(page, "what is the capital of France?").status == "not_found"
    assert "won't guess" in answers.answer(page, "what is the capital of France?").text()
    assert answers.answer(page, "the").status == "empty_question"
    assert answers.answer(reader.extract("<img src=x>", "https://x.org"), "black holes").status == "empty_page"


# --- Saving to Knowledge ------------------------------------------------------------------

def test_save_page_into_documents_for_knowledge(tmp_path):
    page = reader.extract(ARTICLE, "https://space.example/bh")
    first = saving.save(page, saved=datetime(2026, 11, 11, 12, 30))
    assert first == settings.DOCUMENTS_DIR / "Web pages" / "How black holes form Space Site.md"
    content = first.read_text(encoding="utf-8")
    assert content.startswith("# How black holes form | Space Site")
    assert "Source: https://space.example/bh" in content and "Saved: 11 November 2026 12:30" in content
    second = saving.save(page)
    assert second.name == "How black holes form Space Site (2).md" and first.read_text(encoding="utf-8") == content

    from aurora.knowledge import answers as knowledge_answers
    from aurora.knowledge.index import KnowledgeIndex
    index = KnowledgeIndex()
    index.sync()
    found = knowledge_answers.answer(index, "when was a black hole first photographed?")
    assert found.status == "found" and "2019" in found.quotes[0].text
    assert found.quotes[0].document.startswith("Web pages/")


@pytest.mark.parametrize("title, name", [
    ('A/B: "test" <1>?', "A B test 1"), ("CON", "Web page"), ("   ...  ", "Web page"), ("x" * 200, "x" * 80),
])
def test_saved_file_names_are_safe(title, name):
    assert saving.file_name(title) == name


def test_nothing_to_save():
    with pytest.raises(saving.NothingToSave):
        saving.save(reader.extract("<img src=x>", "https://x.org"))


# --- The network gate ---------------------------------------------------------------------

@pytest.fixture
def gate():
    return NetworkGate()


def test_browser_may_open_any_site_but_internet_off_blocks_everything(gate, monkeypatch):
    network = BrowserNetwork(gate)
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "on")
    assert network.decide("https://any-site.example/page").allowed
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "off")
    blocked = network.decide("https://other.example/")
    assert not blocked.allowed and blocked.kind == INTERNET and "switched off" in blocked.reason
    assert network.decide("http://localhost:8000/").allowed                          # this PC
    monkeypatch.setattr(settings, "LOCAL_NETWORK_ACCESS", False)
    assert not network.decide("http://192.168.1.40/").allowed


def test_browser_asks_before_going_online(gate, monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "ask")
    asked = []
    network = BrowserNetwork(gate)
    network.on_ask = asked.append
    decision = network.decide("https://example.org/")
    assert not decision.allowed and decision.can_ask and asked == ["example.org"]
    gate.approve("Browser")
    assert network.decide("https://example.org/").allowed


def test_local_addresses_never_leave_the_pc_and_files_are_refused(gate):
    network = BrowserNetwork(gate)
    for url in ("data:image/png;base64,AAAA", "blob:https://a.org/1", "about:blank", "qrc:///x",
                "chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/index.html"):
        assert network.decide(url) .allowed, url
    assert not network.decide("file:///C:/Users/x/secret.txt").allowed
    assert not network.decide("ftp://example.org/").allowed
    assert gate.records() == []                                   # none of these is a network request


def test_each_host_is_listed_once_and_per_page(gate, monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "on")
    network = BrowserNetwork(gate)
    page = "https://www.bbc.co.uk/news"
    for url in (page, "https://static.files.bbc.co.uk/a.css", "https://static.files.bbc.co.uk/b.js",
                "https://tracker.example/pixel.gif", "https://tracker.example/pixel2.gif"):
        network.decide(url, page)
    records = gate.records()
    assert [r.host for r in records] == ["www.bbc.co.uk", "static.files.bbc.co.uk", "tracker.example"]
    assert all(r.component == "Browser" and r.outcome == "sent" and r.kind == INTERNET for r in records)
    assert network.contacted(page) == [("www.bbc.co.uk", True), ("static.files.bbc.co.uk", True),
                                       ("tracker.example", True)]
    assert is_other_site("tracker.example", page) and not is_other_site("static.files.bbc.co.uk", page)
    network.forget_page(page)
    assert network.contacted(page) == []


def test_records_never_contain_the_path_or_query(gate, monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "on")
    BrowserNetwork(gate).decide("https://duckduckgo.com/?q=my+private+question")
    described = gate.records()[0].describe()
    assert "duckduckgo.com" in described and "private" not in described


def test_gate_decide_matches_check_for_other_services(gate, monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "on")
    kind, reason, _ = gate.decide("Weather", "evil.example")
    assert reason and "destinations" in reason                    # services without any_host stay restricted
    assert gate.decide("Weather", "api.open-meteo.com") == (INTERNET, "", False)
    assert gate.decide("Browser", "")[1]                            # no host: refused
    assert gate.decide("Browser", "127.0.0.1")[0] == THIS_PC and gate.decide("Browser", "10.0.0.5")[0] == LOCAL


# --- The Browser page (fake web engine) ---------------------------------------------------

@pytest.fixture(scope="module")
def qapp_browser():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _fakes():
    from PySide6.QtCore import QObject, QUrl, Signal
    from PySide6.QtWidgets import QWidget

    class FakePage:
        def __init__(self, view):
            self.view = view

        def toHtml(self, callback):
            callback(self.view.markup)

    class FakeView(QWidget):
        titleChanged = Signal(str)
        urlChanged = Signal(QUrl)
        loadFinished = Signal(bool)

        def __init__(self, network):
            super().__init__()
            self.network = network
            self._url, self._title, self.markup, self.found = QUrl("about:blank"), "", "", []
            self._page = FakePage(self)

        def setUrl(self, url):
            self._url = url
            self.network.decide(url.toString(), url.toString())       # as the interceptor would
            self.urlChanged.emit(url)
            pages = FakeFactory.pages
            self.markup = pages.get(url.toString(), "<html><title>Some page</title><p>Nothing much.</p></html>")
            self._title = reader.extract(self.markup, url.toString()).title
            self.titleChanged.emit(self._title)
            self.loadFinished.emit(True)

        def setHtml(self, markup, base):
            self._url, self.markup = base, markup

        def url(self):
            return self._url

        def title(self):
            return self._title

        def page(self):
            return self._page

        def findText(self, text):
            self.found.append(text)

        def back(self): pass
        def forward(self): pass
        def reload(self): pass

    class FakeFactory:
        pages = {}

        def __init__(self, network):
            self.network = network
            self.profile = None
            self.cleared = False

        def view(self, open_tab, parent=None):
            return FakeView(self.network)

        def clear_site_data(self):
            self.cleared = True

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

        def on_gui(self, callback, value=None):
            callback(value)

    return FakeFactory, Backend


@pytest.fixture
def browser_page(qapp_browser, monkeypatch, gate):
    from PySide6.QtWidgets import QMessageBox

    from aurora.interface import browser_view
    monkeypatch.setattr(browser_view, "network_gate", gate)
    answers_given = []
    monkeypatch.setattr(browser_view.QMessageBox, "question",
                        staticmethod(lambda *a, **k: answers_given.pop(0) if answers_given else QMessageBox.Yes))
    Factory, Backend = _fakes()
    Factory.pages = {"https://space.example/bh": ARTICLE}
    view = browser_view.BrowserView(Backend(), factory=Factory, network=BrowserNetwork(gate))
    view.answers_given = answers_given
    yield view
    from conftest import release_widgets
    release_widgets()


def test_browser_page_starts_the_engine_only_when_opened(browser_page):
    assert browser_page.factory is None and browser_page.tabs.count() == 0
    browser_page.on_shown()
    assert browser_page.factory is not None and browser_page.tabs.count() == 1


def test_browser_page_asks_then_opens_and_records_history(browser_page, gate, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "ask")
    browser_page.on_shown()
    browser_page.answers_given.append(QMessageBox.No)
    browser_page.navigate("space.example/bh")
    assert browser_page.current().url().toString() == "about:blank"           # declined: nothing loaded
    assert "nothing was sent" in browser_page.status.text()
    browser_page.navigate("space.example/bh")                                  # asked again, yes this time
    assert gate.approved() == ["Browser"]
    assert browser_page.current().url().toString() == "https://space.example/bh"
    assert browser_page.tabs.tabText(0).startswith("How black holes form")
    assert [v.url for v in browser_page.store.history()] == ["https://space.example/bh"]


def test_browser_page_refuses_when_internet_is_off(browser_page, monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "off")
    browser_page.on_shown()
    browser_page.navigate("space.example/bh")
    assert browser_page.current().url().toString() == "about:blank"
    assert "switched off" in browser_page.status.text()
    browser_page.navigate("javascript:alert(1)")
    assert "only opens web pages" in browser_page.status.text()


def test_browser_page_ask_reader_save_and_bookmark(browser_page, monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "on")
    browser_page.on_shown()
    browser_page.navigate("https://space.example/bh")
    browser_page.question.setText("when was a black hole first photographed?")
    browser_page.ask()
    assert "2019" in browser_page.answer_view.toPlainText()
    browser_page.show_reader()
    assert "massive star" in browser_page.reader_view.toPlainText()
    assert "cookies" not in browser_page.reader_view.toPlainText()
    browser_page.save_page()
    assert "Saved to Documents/Web pages/" in browser_page.status.text()
    assert (settings.DOCUMENTS_DIR / "Web pages" / "How black holes form Space Site.md").is_file()
    browser_page.toggle_bookmark()
    assert browser_page.star.text() == "★" and browser_page.bookmark_list.count() == 1
    browser_page.toggle_bookmark()
    assert browser_page.star.text() == "☆" and browser_page.store.bookmarks() == []
    browser_page.find_box.setText("telescope")
    browser_page.find()
    assert browser_page.current().found == ["telescope"]


def test_browser_page_limits_tabs_and_history_switch(browser_page, monkeypatch, tmp_path):
    from aurora.config import preferences
    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    monkeypatch.setattr(settings, "BROWSER_MAX_TABS", 2)
    browser_page.on_shown()
    assert browser_page.new_tab() is not None
    assert browser_page.new_tab() is None and "At most 2 tabs" in browser_page.status.text()
    browser_page.close_tab(1)
    assert browser_page.tabs.count() == 1
    browser_page.keep_history.setChecked(False)
    assert settings.BROWSER_HISTORY is False and preferences.load()["browser_history"] is False
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "on")
    browser_page.navigate("https://space.example/bh")
    assert browser_page.store.history() == []


def test_browser_page_lists_sites_contacted(browser_page, gate, monkeypatch):
    monkeypatch.setattr(settings, "INTERNET_ACCESS", "on")
    browser_page.on_shown()
    browser_page.navigate("https://space.example/bh")
    browser_page.network.decide("https://cdn.tracker.example/p.gif", "https://space.example/bh")
    browser_page.side.setCurrentIndex(2)                          # Sites
    items = [browser_page.sites.item(i).text() for i in range(browser_page.sites.count())]
    assert items[0] == "space.example" and "cdn.tracker.example  · other site" in items


def test_browser_engine_failure_is_reported(qapp_browser):
    from aurora.interface import browser_view
    _, Backend = _fakes()
    view = browser_view.BrowserView(Backend())          # conftest blocks the real engine
    view.on_shown()
    assert "couldn't start" in view.status.text() and view.tabs.count() == 0
