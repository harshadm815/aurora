"""
News Centre: search, compare publishers and investigate a story, with
filters, article cards (publisher, date, link, verification), and saved
articles in folders.

Searches run only when you press a button (the News tool, so its
permission and the network gate apply). If going online needs your OK,
the page asks first, saying where the request goes and what is sent.
"""

import html
import re
from datetime import timedelta

from PySide6.QtCore import QDate, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QDateEdit, QHBoxLayout, QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import label
from aurora.network import NetworkBlocked, gate as network_gate
from aurora.news.query import CATEGORIES, COMPARE, INVESTIGATE, SEARCH, parse
from aurora.news.report import checks, compare_text, coverage, investigation_text, link, when
from aurora.news.saved import SavedArticles

PERIODS = [("Past week", 7), ("Today", 0), ("Past 3 days", 3), ("Past month", 30), ("Past 3 months", 90),
           ("Past year", 365), ("Custom dates", None)]
_URL = re.compile(r"https?://[^\s)<>\"]+")


def text_to_html(text: str) -> str:
    """Plain report text as HTML: escaped, with links clickable."""
    escaped = html.escape(text)
    linked = _URL.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)[:90]}{"…" if len(m.group(0)) > 90 else ""}</a>',
                      escaped)
    return f"<div style='white-space: pre-wrap'>{linked}</div>"


def _names(text: str) -> tuple[str, ...]:
    return tuple(n.strip() for n in re.split(r"[,;]", text or "") if n.strip())


class NewsView(BaseView):
    title = "News Centre"
    subtitle = ("Searches the news only when you ask: GDELT, Google News, publisher feeds and (if set up) web search. "
                "Every story shows its sources and how well it is confirmed.")
    nav_icon = "📰"
    scrollable = False

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.saved = SavedArticles()
        self.report = None
        self.tabs = QTabWidget()
        self.tabs.addTab(self._search_tab(), "Search")
        self.tabs.addTab(self._saved_tab(), "Saved articles")
        self.body.addWidget(self.tabs, 1)

    # --- Search ----------------------------------------------------------------------

    def _search_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("e.g. murder cases in India this week · scientific discoveries this month · "
                                           "the Pune bridge collapse")
        self.search_box.returnPressed.connect(lambda: self.search(SEARCH))
        row.addWidget(self.search_box, 1)
        for text, mode in (("Search", SEARCH), ("Compare publishers", COMPARE), ("Investigate story", INVESTIGATE)):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, m=mode: self.search(m))
            if mode == SEARCH:
                button.setObjectName("Primary")
            row.addWidget(button)
        layout.addLayout(row)

        filters = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItem("Any category", "")
        for name in CATEGORIES:
            self.category.addItem(name.capitalize(), name)
        self.place = QLineEdit()
        self.place.setPlaceholderText("Country, region or city (blank: worldwide)")
        self.period = QComboBox()
        for text, days in PERIODS:
            self.period.addItem(text, days)
        self.period.currentIndexChanged.connect(self._period_changed)
        self.since = QDateEdit(QDate.currentDate().addDays(-7))
        self.until = QDateEdit(QDate.currentDate())
        for edit in (self.since, self.until):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd MMM yyyy")
            edit.setEnabled(False)
        for widget in (self.category, self.place, self.period, self.since, self.until):
            filters.addWidget(widget)
        layout.addLayout(filters)
        sources = QHBoxLayout()
        self.only = QLineEdit()
        self.only.setPlaceholderText("Only from (e.g. BBC, The Hindu)")
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText("Not from")
        self.folder = QComboBox()
        self.folder.setEditable(True)
        self.folder.addItems(self.saved.folders() or ["Saved"])
        self.folder.setToolTip("Folder that Save puts articles in")
        sources.addWidget(self.only, 1)
        sources.addWidget(self.exclude, 1)
        sources.addWidget(label("Save to:", "Dim"))
        sources.addWidget(self.folder)
        layout.addLayout(sources)

        self.results = QTextBrowser()
        self.results.setOpenLinks(False)
        self.results.anchorClicked.connect(self._clicked)
        self.results.setHtml(text_to_html(
            "Nothing is searched until you ask. Type a topic (or leave it blank for top stories) and press Search."))
        layout.addWidget(self.results, 1)
        self.status = label("", "Faint", wrap=True)
        layout.addWidget(self.status)
        return tab

    def _period_changed(self):
        custom = self.period.currentData() is None
        self.since.setEnabled(custom)
        self.until.setEnabled(custom)

    def build_query(self, mode: str):
        text = self.search_box.text().strip()
        query = parse(text or "news", feeds=settings.NEWS_FEEDS)
        today = QDate.currentDate().toPython()
        days = self.period.currentData()
        if days is None:
            since, until = self.since.date().toPython(), self.until.date().toPython()
            query = query.with_dates(min(since, until), max(since, until))
        elif not text or query.period in ("the past week", "the past 3 months"):   # the filter, not a default
            query = query.with_dates(today - timedelta(days=days), today, self.period.currentText().lower())
        changes = {"mode": mode, "compare_count": query.compare_count or (5 if mode == COMPARE else 0)}
        if self.category.currentData():
            changes["category"] = self.category.currentData()
        if self.place.text().strip():
            changes["place"] = self.place.text().strip()
        if self.only.text().strip():
            changes["publishers"] = _names(self.only.text())
        if self.exclude.text().strip():
            changes["exclude"] = _names(self.exclude.text())
        from dataclasses import replace
        return replace(query, **changes)

    def search(self, mode: str = SEARCH):
        engine = self.backend.engine
        if engine is None or engine.tool_manager.get("News") is None:
            self.results.setHtml(text_to_html("The News tool isn't available."))
            return
        query = self.build_query(mode)
        self.status.setText(f"Searching: {query.describe()}…  Only these words are sent: “{query.terms() or 'top stories'}”.")

        def run():
            return engine.tool_manager.execute("News", query=query)

        self.backend.run_io(run, self._show, self._failed(query, mode))

    def _failed(self, query, mode):
        def failed(error):
            if isinstance(error, NetworkBlocked) and error.can_ask:
                service = settings.NETWORK_SERVICES.get("News", {})
                answer = QMessageBox.question(
                    self, "Go online for this search?",
                    f"News needs the internet for this search. It would send {service.get('sends', 'your search terms')} "
                    f"(“{query.terms() or 'top stories'}”) to {error.host} and the other news services.\n\n"
                    "Allow News to go online until AURORA restarts?")
                if answer == QMessageBox.Yes:
                    network_gate.approve("News")
                    self.search(mode)
                else:
                    self.status.setText("Not searched: nothing was sent.")
                return
            if isinstance(error, NetworkBlocked):
                self.status.setText(f"Not searched: {error.reason}. See Settings → Internet and network.")
                return
            self.status.setText(f"The search didn't work: {error}")
        return failed

    def _show(self, result):
        report = getattr(result, "data", None)
        if report is None or not hasattr(report, "stories"):
            self.results.setHtml(text_to_html(getattr(result, "message", "No result.")))
            self.status.setText("")
            return
        self.report = report
        mode = report.query.mode
        if mode == INVESTIGATE:
            self.results.setHtml(text_to_html(investigation_text(report)))
        elif mode == COMPARE:
            self.results.setHtml(text_to_html(compare_text(report, 20)))
        else:
            self.results.setHtml(self.cards(report))
        self.status.setText(coverage(report))

    def cards(self, report) -> str:
        if not report.stories:
            return text_to_html("No reports matched in the sources searched. AURORA doesn't fill the gap with guesses.")
        parts = [f"<p><b>{html.escape(report.query.describe())}</b>: {len(report.stories)} stories</p>"]
        for number, story in enumerate(report.stories):
            lead = story.lead
            notes = "".join(f"<li>{html.escape(n)}</li>" for n in checks(story))
            others = [a for a in story.articles if a is not lead][:4]
            also = "".join(f"<li>{html.escape(a.publisher)}: <a href=\"{html.escape(a.url)}\">"
                           f"{html.escape(a.title)}</a> ({html.escape(when(a))})</li>" for a in others)
            via = " (via Google News)" if not lead.original_url_known else ""
            parts.append(
                f"<h3><a href=\"{html.escape(lead.url)}\">{html.escape(lead.title)}</a></h3>"
                f"<p>{html.escape(lead.publisher)} · {html.escape(when(lead))}"
                f"{' · ' + html.escape(lead.category) if story.category else ''}{via}</p>"
                + (f"<p>{html.escape(lead.summary)}</p>" if lead.summary else "")
                + f"<ul>{notes}</ul>"
                + (f"<p>Also reported:</p><ul>{also}</ul>" if also else "")
                + f"<p><a href=\"save:{number}\">Save to “{html.escape(self.folder.currentText() or 'Saved')}”</a></p><hr>")
        return "".join(parts)

    def _clicked(self, url: QUrl):
        target = url.toString()
        if target.startswith("save:") and self.report is not None:
            index = int(target[5:])
            if 0 <= index < len(self.report.stories):
                article = self.report.stories[index].lead
                new = self.saved.save(article, self.folder.currentText())
                self.status.setText(("Saved: " if new else "Already saved (folder updated): ") + article.title)
                self.load_saved()
            return
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)          # only when you click a link

    # --- Saved ----------------------------------------------------------------------------

    def _saved_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        row = QHBoxLayout()
        self.saved_folder = QComboBox()
        self.saved_folder.currentIndexChanged.connect(self._fill_saved)
        row.addWidget(label("Folder:", "Dim"))
        row.addWidget(self.saved_folder)
        row.addStretch(1)
        for text, handler in (("Open", self.open_saved), ("Move to folder…", self.move_saved),
                              ("Delete", self.delete_saved)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        self.saved_list = QListWidget()
        self.saved_list.itemDoubleClicked.connect(lambda _: self.open_saved())
        layout.addWidget(self.saved_list, 1)
        layout.addWidget(label(f"Saved on this PC only ({settings.NEWS_DATABASE_PATH.name}): the headline, publisher, "
                               "date, description and link, plus your folder.", "Faint", wrap=True))
        self.load_saved()
        return tab

    def load_saved(self):
        current = self.saved_folder.currentData() if self.saved_folder.count() else None
        self.saved_folder.blockSignals(True)
        self.saved_folder.clear()
        self.saved_folder.addItem("All folders", None)
        for folder in self.saved.folders():
            self.saved_folder.addItem(folder, folder)
        index = self.saved_folder.findData(current)
        self.saved_folder.setCurrentIndex(max(0, index))
        self.saved_folder.blockSignals(False)
        self._fill_saved()

    def _fill_saved(self):
        self.saved_list.clear()
        for article in self.saved.all(self.saved_folder.currentData()):
            date = f"{article.published:%d %b %Y}" if article.published else "date not given"
            item = QListWidgetItem(f"{article.title}  ·  {article.publisher}  ·  {date}  ·  [{article.folder}]")
            item.setData(256, article)
            item.setToolTip(article.summary or article.url)
            self.saved_list.addItem(item)

    def _selected(self):
        item = self.saved_list.currentItem()
        return item.data(256) if item is not None else None

    def open_saved(self):
        article = self._selected()
        if article is not None and article.url.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(article.url))

    def move_saved(self):
        article = self._selected()
        if article is None:
            return
        folder, ok = QInputDialog.getText(self, "Move to folder", "Folder name:", text=article.folder)
        if ok and folder.strip():
            self.saved.move(article.id, folder)
            self.load_saved()

    def delete_saved(self):
        article = self._selected()
        if article is None:
            return
        if QMessageBox.question(self, "Delete saved article", f"Delete “{article.title}” from your saved articles?") \
                == QMessageBox.Yes:
            self.saved.delete(article.id)
            self.load_saved()
