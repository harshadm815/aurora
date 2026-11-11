"""
The Browser page: the AURORA Browser inside the Command Centre.

    toolbar     back, forward, reload, address bar (addresses or searches),
                bookmark, find in page, new tab
    tabs        web pages (Qt WebEngine), up to BROWSER_MAX_TABS
    side panel  Ask this page · Reader · Sites contacted · History · Bookmarks

Privacy and control (see aurora/browser/network.py):
- Chromium starts only when you first open this page.
- Every request goes through the network gate. With internet access on
  "ask", the first page each session asks you first.
- Each page's "Sites contacted" list shows every site it loaded something
  from, and System -> Network activity lists them too.
- Sites can't use the camera, microphone, location or notifications.
- Downloads happen only after you confirm and choose where to save.
- History is on this PC only, can be switched off, and is cleared after
  BROWSER_HISTORY_DAYS days. Cookies and cache stay in AURORA's own
  browser profile.
"""

import html
import logging
from pathlib import Path

from PySide6.QtCore import QStringListModel, Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QCompleter, QFileDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSplitter, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from aurora.browser import addresses, answers, reader, saving
from aurora.browser.network import COMPONENT, BrowserNetwork, is_other_site
from aurora.browser.store import BrowserStore
from aurora.config import preferences, settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import label
from aurora.network import gate as network_gate

logger = logging.getLogger(__name__)

START_PAGE = """<html><head><title>New tab</title><style>
body{font-family:Segoe UI,sans-serif;background:#0f172a;color:#cbd5e1;margin:12% auto;max-width:560px;text-align:center}
h1{color:#67e8f9;font-weight:600} p{line-height:1.5}</style></head><body>
<h1>AURORA Browser</h1><p>Type an address or a search in the bar above.</p>
<p>Pages load only when you open them. Every site a page contacts is listed under
<b>Sites contacted</b>, and AURORA can quote this page to answer your questions.</p></body></html>"""


def _qt_web_factory(network):
    from aurora.interface.browser_web import WebFactory      # starts Qt WebEngine only now
    return WebFactory(network)


class BrowserView(BaseView):
    title = "Browser"
    subtitle = ("Browse the web inside AURORA. Pages load only when you open them; every site they contact is "
                "listed. Ask about a page and AURORA answers by quoting it.")
    nav_icon = "🌐"
    scrollable = False

    def __init__(self, backend, parent=None, factory=None, store=None, network=None):
        super().__init__(backend, parent)
        self.store = store or BrowserStore()
        self.network = network or BrowserNetwork()
        self.network.on_ask = lambda host: self.backend.on_gui(self._ask_later, host)
        self._factory_maker = factory or _qt_web_factory
        self.factory = None
        self._asking = False
        self._declined = False
        self.downloads: list = []
        try:
            removed = self.store.prune()
            if removed:
                logger.info("Browser history: %d old visit(s) deleted", removed)
        except Exception:
            logger.exception("Pruning browser history failed")

        self.body.addLayout(self._toolbar())
        split = QSplitter(Qt.Horizontal)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        split.addWidget(self.tabs)
        split.addWidget(self._side_panel())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([900, 340])
        self.body.addWidget(split, 1)
        self.status = label("", "Faint", wrap=True)
        self.body.addWidget(self.status)
        QShortcut(QKeySequence.Find, self, activated=lambda: self.find_box.setFocus())
        QShortcut(QKeySequence("Ctrl+T"), self, activated=lambda: self.new_tab())
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self.close_tab(self.tabs.currentIndex()))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: (self.address.setFocus(), self.address.selectAll()))

    # ------------------------------------------------------------------ layout

    def _toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        buttons = (("◀", "Back", lambda: self._current_call("back")),
                   ("▶", "Forward", lambda: self._current_call("forward")),
                   ("⟳", "Reload", lambda: self._current_call("reload")))
        for text, tip, handler in buttons:
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(handler)
            row.addWidget(button)
        self.address = QLineEdit()
        self.address.setPlaceholderText("Address or search (e.g. arxiv.org, or black hole formation)")
        self.address.returnPressed.connect(lambda: self.navigate(self.address.text()))
        self.suggestions = QStringListModel(self)
        completer = QCompleter(self.suggestions, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(lambda text: self.navigate(text.split("  —  ")[0]))
        self.address.setCompleter(completer)
        self.address.textEdited.connect(self._suggest)
        row.addWidget(self.address, 1)
        self.star = QToolButton()
        self.star.setText("☆")
        self.star.setToolTip("Bookmark this page")
        self.star.clicked.connect(self.toggle_bookmark)
        row.addWidget(self.star)
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find in page")
        self.find_box.setMaximumWidth(170)
        self.find_box.returnPressed.connect(self.find)
        row.addWidget(self.find_box)
        new = QToolButton()
        new.setText("+")
        new.setToolTip("New tab (Ctrl+T)")
        new.clicked.connect(lambda: self.new_tab())
        row.addWidget(new)
        return row

    def _side_panel(self) -> QWidget:
        self.side = QTabWidget()
        self.side.addTab(self._ask_tab(), "Ask")
        self.side.addTab(self._reader_tab(), "Reader")
        self.side.addTab(self._sites_tab(), "Sites")
        self.side.addTab(self._history_tab(), "History")
        self.side.addTab(self._bookmarks_tab(), "Bookmarks")
        self.side.setMinimumWidth(300)
        self.side.currentChanged.connect(lambda _: self._side_shown())
        return self.side

    def _ask_tab(self) -> QWidget:
        tab, layout = QWidget(), QVBoxLayout()
        tab.setLayout(layout)
        layout.addWidget(label("Ask about the open page. AURORA quotes the page's own sentences; "
                               "if the page doesn't answer it, it says so.", "Faint", wrap=True))
        self.question = QLineEdit()
        self.question.setPlaceholderText("e.g. when was it discovered?")
        self.question.returnPressed.connect(self.ask)
        layout.addWidget(self.question)
        row = QHBoxLayout()
        ask = QPushButton("Ask")
        ask.setObjectName("Primary")
        ask.clicked.connect(self.ask)
        save = QPushButton("Save to Knowledge")
        save.setToolTip(f"Saves the page's text to Documents/{settings.BROWSER_SAVED_FOLDER}, "
                        "so your document questions can quote it")
        save.clicked.connect(self.save_page)
        row.addWidget(ask)
        row.addWidget(save)
        layout.addLayout(row)
        self.answer_view = QTextBrowser()
        self.answer_view.setOpenExternalLinks(False)
        layout.addWidget(self.answer_view, 1)
        return tab

    def _reader_tab(self) -> QWidget:
        tab, layout = QWidget(), QVBoxLayout()
        tab.setLayout(layout)
        refresh = QPushButton("Read this page")
        refresh.clicked.connect(self.show_reader)
        layout.addWidget(refresh)
        self.reader_view = QTextBrowser()
        self.reader_view.setOpenLinks(False)
        layout.addWidget(self.reader_view, 1)
        return tab

    def _sites_tab(self) -> QWidget:
        tab, layout = QWidget(), QVBoxLayout()
        tab.setLayout(layout)
        layout.addWidget(label("Every site the open page loaded something from (images, scripts, fonts, "
                               "trackers). Blocked ones were stopped by the network settings.", "Faint", wrap=True))
        self.sites = QListWidget()
        layout.addWidget(self.sites, 1)
        clear = QPushButton("Clear this browser's cookies and cache")
        clear.clicked.connect(self.clear_site_data)
        layout.addWidget(clear)
        return tab

    def _history_tab(self) -> QWidget:
        tab, layout = QWidget(), QVBoxLayout()
        tab.setLayout(layout)
        self.keep_history = QCheckBox("Keep history")
        self.keep_history.setChecked(bool(settings.BROWSER_HISTORY))
        self.keep_history.toggled.connect(self.set_history)
        layout.addWidget(self.keep_history)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search history")
        self.history_search.textChanged.connect(lambda _: self.load_history())
        layout.addWidget(self.history_search)
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(lambda item: self.navigate(item.data(256).url))
        layout.addWidget(self.history_list, 1)
        row = QHBoxLayout()
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_visit)
        clear = QPushButton("Clear all history")
        clear.clicked.connect(self.clear_history)
        row.addWidget(delete)
        row.addWidget(clear)
        layout.addLayout(row)
        layout.addWidget(label(f"On this PC only; deleted after {settings.BROWSER_HISTORY_DAYS} days.", "Faint",
                               wrap=True))
        return tab

    def _bookmarks_tab(self) -> QWidget:
        tab, layout = QWidget(), QVBoxLayout()
        tab.setLayout(layout)
        self.bookmark_list = QListWidget()
        self.bookmark_list.itemDoubleClicked.connect(lambda item: self.navigate(item.data(256).url))
        layout.addWidget(self.bookmark_list, 1)
        remove = QPushButton("Remove bookmark")
        remove.clicked.connect(self.remove_bookmark)
        layout.addWidget(remove)
        return tab

    # -------------------------------------------------------------- tabs and pages

    def on_shown(self):
        if self.factory is None:
            try:
                self.factory = self._factory_maker(self.network)
                profile = getattr(self.factory, "profile", None)
                if profile is not None and hasattr(profile, "downloadRequested"):
                    profile.downloadRequested.connect(self._download)
            except Exception as error:
                logger.exception("The browser engine couldn't start")
                self.status.setText(f"The browser engine couldn't start ({type(error).__name__}). It needs the "
                                    "PySide6-Addons and PySide6-WebEngine packages (see Requirements.txt).")
                return
        if self.tabs.count() == 0:
            self.new_tab()
        self._side_shown()

    def new_tab(self, url: str | None = None):
        """Opens a tab (start page, or url). Returns its view, or None at the tab limit."""
        if self.factory is None:
            return None
        if self.tabs.count() >= settings.BROWSER_MAX_TABS:
            self.status.setText(f"At most {settings.BROWSER_MAX_TABS} tabs (each uses 100-300 MB of memory). "
                                "Close one to open another.")
            return None
        view = self.factory.view(self.new_tab)
        view.titleChanged.connect(lambda title, v=view: self._title_changed(v, title))
        view.urlChanged.connect(lambda qurl, v=view: self._url_changed(v, qurl))
        view.loadFinished.connect(lambda ok, v=view: self._load_finished(v, ok))
        index = self.tabs.addTab(view, "New tab")
        self.tabs.setCurrentIndex(index)
        if url:
            self._load(view, url)
        else:
            view.setHtml(START_PAGE, QUrl("about:blank"))
            self.address.clear()
            self.address.setFocus()
        return view

    def close_tab(self, index: int):
        if index < 0:
            return
        view = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if view is not None:
            view.deleteLater()

    def current(self):
        return self.tabs.currentWidget()

    def _current_call(self, method: str):
        view = self.current()
        if view is not None:
            getattr(view, method)()

    def navigate(self, text: str):
        """Opens what was typed in the current tab (a new one if there is none)."""
        try:
            url = addresses.resolve(text)
        except addresses.RefusedAddress as refused:
            self.status.setText(str(refused))
            return
        view = self.current() or self.new_tab()
        if view is not None:
            self._load(view, url)

    def _load(self, view, url: str):
        if url != addresses.BLANK and not self._may_go_online(url):
            return
        self.network.forget_page(url)
        view.setUrl(QUrl(url))
        self.address.setText(url)
        view.setFocus()

    def _may_go_online(self, url: str) -> bool:
        """Asks before the first page of the session when internet access is on "ask"."""
        kind, reason, can_ask = network_gate.decide(COMPONENT, addresses.host_of(url))
        if not reason:
            return True
        if not can_ask:
            self.status.setText(f"Not opened: {reason}. See Settings → Internet and network.")
            return False
        self._declined = False
        return self._ask(addresses.host_of(url))

    def _ask(self, host: str) -> bool:
        if self._asking:
            return False
        self._asking = True
        try:
            answer = QMessageBox.question(
                self, "Go online with the AURORA Browser?",
                f"The page you asked for is on the internet ({host}). Pages also load parts from other sites; "
                "every site contacted is listed under Sites.\n\n"
                "Allow the AURORA Browser to go online until AURORA restarts?")
        finally:
            self._asking = False
        if answer == QMessageBox.Yes:
            network_gate.approve(COMPONENT)
            self.status.setText("The AURORA Browser may go online until AURORA restarts.")
            return True
        self._declined = True
        self.status.setText("Not opened: nothing was sent.")
        return False

    def _ask_later(self, host: str):
        """A page wanted to go online while it wasn't approved (a link or redirect)."""
        if self._asking or self._declined or COMPONENT in network_gate.approved():
            return
        if self._ask(host):
            self._current_call("reload")

    def _title_changed(self, view, title: str):
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabText(index, (title or "New tab")[:28])
            self.tabs.setTabToolTip(index, title)
        url = view.url().toString()
        if addresses.is_web(url):
            self.store.record_visit(url, title)

    def _url_changed(self, view, qurl):
        if view is self.current():
            text = qurl.toString()
            self.address.setText("" if text == addresses.BLANK else text)
            self._update_star()

    def _load_finished(self, view, ok: bool):
        url = view.url().toString()
        if addresses.is_web(url):
            self.store.record_visit(url, view.title())
        if view is self.current():
            self._side_shown()
            if not ok and addresses.is_web(url):
                blocked = [host for host, allowed in self.network.contacted(url) if not allowed]
                self.status.setText("The page didn't load" + (f" (blocked: {', '.join(blocked[:3])})" if blocked
                                                             else "") + ".")
            elif ok:
                self.status.setText("")

    def _tab_changed(self, _index: int):
        view = self.current()
        if view is None:
            self.address.clear()
            return
        text = view.url().toString()
        self.address.setText("" if text == addresses.BLANK else text)
        self._update_star()
        self._side_shown()

    def find(self):
        view = self.current()
        if view is not None:
            view.findText(self.find_box.text())

    def _suggest(self, typed: str):
        self.suggestions.setStringList([f"{url}  —  {title}" for url, title in self.store.suggestions(typed)])

    # ---------------------------------------------------------- reading the page

    def _with_page(self, then):
        """Calls then(ReaderPage) with the open page's text (read in the background)."""
        view = self.current()
        url = view.url().toString() if view is not None else ""
        if view is None or not addresses.is_web(url):
            self.status.setText("Open a web page first.")
            return

        def got_html(markup):
            self.backend.run_io(lambda: reader.extract(markup or "", url), then,
                                lambda error: self.status.setText(f"Reading the page didn't work: {error}"))

        view.page().toHtml(got_html)

    def ask(self):
        question = self.question.text().strip()
        if not question:
            return
        self.answer_view.setPlainText("Reading the page…")
        self._with_page(lambda page: self.answer_view.setPlainText(answers.answer(page, question).text()))

    def show_reader(self):
        self.reader_view.setPlainText("Reading the page…")
        self._with_page(self._show_reader)

    def _show_reader(self, page: reader.ReaderPage):
        if not page.blocks:
            self.reader_view.setPlainText("No article text found on this page (it may be mostly pictures, "
                                          "a PDF, or still loading).")
            return
        parts = [f"<h2>{html.escape(page.title)}</h2>", f"<p><i>{html.escape(page.url)}</i></p>"]
        for block in page.blocks:
            text = html.escape(block.text)
            if block.heading:
                parts.append(f"<h3>{text}</h3>")
            elif block.kind == "li":
                parts.append(f"<p>• {text}</p>")
            elif block.kind == "code":
                parts.append(f"<pre>{text}</pre>")
            else:
                parts.append(f"<p>{text}</p>")
        self.reader_view.setHtml("".join(parts))

    def save_page(self):
        def save(page):
            try:
                path = saving.save(page)
            except saving.NothingToSave as nothing:
                self.status.setText(str(nothing))
                return
            except OSError as error:
                self.status.setText(f"Couldn't save the page: {error.strerror or error}")
                return
            relative = Path(path).relative_to(Path(settings.DOCUMENTS_DIR)).as_posix()
            self.status.setText(f"Saved to Documents/{relative}. Knowledge will include it in your "
                                "document questions.")
        self._with_page(save)

    # ------------------------------------------------------------- side panels

    def _side_shown(self):
        name = self.side.tabText(self.side.currentIndex())
        if name == "Sites":
            self.load_sites()
        elif name == "History":
            self.load_history()
        elif name == "Bookmarks":
            self.load_bookmarks()

    def load_sites(self):
        self.sites.clear()
        view = self.current()
        url = view.url().toString() if view is not None else ""
        if not addresses.is_web(url):
            return
        for host, allowed in self.network.contacted(url):
            note = "" if allowed else "  (blocked)"
            other = "  · other site" if is_other_site(host, url) else ""
            self.sites.addItem(f"{host}{other}{note}")

    def load_history(self):
        self.history_list.clear()
        for visit in self.store.history(self.history_search.text()):
            item = QListWidgetItem(f"{visit.time:%d %b %H:%M}  {visit.title}")
            item.setToolTip(visit.url)
            item.setData(256, visit)
            self.history_list.addItem(item)

    def delete_visit(self):
        item = self.history_list.currentItem()
        if item is not None:
            self.store.delete_visit(item.data(256).id)
            self.load_history()

    def clear_history(self):
        if QMessageBox.question(self, "Clear history", "Delete all of the AURORA Browser's history?") \
                == QMessageBox.Yes:
            count = self.store.clear_history()
            self.status.setText(f"History cleared ({count} visit(s) deleted).")
            self.load_history()

    def set_history(self, keep: bool):
        settings.BROWSER_HISTORY = keep
        try:
            values = preferences.load()
            values["browser_history"] = keep
            preferences.save(values)
        except OSError:
            logger.warning("Couldn't save the history preference")
        self.status.setText("History is on." if keep else "History is off: pages you open aren't recorded.")

    def load_bookmarks(self):
        self.bookmark_list.clear()
        for bookmark in self.store.bookmarks():
            item = QListWidgetItem(f"{bookmark.title}  ·  {addresses.host_of(bookmark.url)}")
            item.setToolTip(bookmark.url)
            item.setData(256, bookmark)
            self.bookmark_list.addItem(item)

    def toggle_bookmark(self):
        view = self.current()
        url = view.url().toString() if view is not None else ""
        if not addresses.is_web(url):
            self.status.setText("Only web pages can be bookmarked.")
            return
        existing = self.store.bookmark_for(url)
        if existing is not None:
            self.store.remove_bookmark(existing.id)
            self.status.setText("Bookmark removed.")
        else:
            self.store.add_bookmark(url, view.title())
            self.status.setText("Bookmarked.")
        self._update_star()
        self.load_bookmarks()

    def remove_bookmark(self):
        item = self.bookmark_list.currentItem()
        if item is not None:
            self.store.remove_bookmark(item.data(256).id)
            self.load_bookmarks()
            self._update_star()

    def _update_star(self):
        view = self.current()
        url = view.url().toString() if view is not None else ""
        marked = addresses.is_web(url) and self.store.bookmark_for(url) is not None
        self.star.setText("★" if marked else "☆")

    def clear_site_data(self):
        if self.factory is None:
            return
        if QMessageBox.question(self, "Clear cookies and cache",
                                "Delete the AURORA Browser's cookies, cache and site data? You'll be signed out "
                                "of sites in this browser (not in your other browsers).") == QMessageBox.Yes:
            self.factory.clear_site_data()
            self.status.setText("Cookies and cache cleared.")

    # --------------------------------------------------------------- downloads

    def _download(self, download):
        name = download.suggestedFileName() or "download"
        host = addresses.host_of(download.url().toString())
        size = download.totalBytes()
        size_text = f"{size / 1048576:.1f} MB" if size and size > 0 else "size unknown"
        answer = QMessageBox.question(self, "Download a file?",
                                      f"{host} wants to download “{name}” ({size_text}).\n\nDownload it?")
        if answer != QMessageBox.Yes:
            download.cancel()
            self.status.setText("Download cancelled.")
            return
        default = Path.home() / "Downloads" / name
        path, _ = QFileDialog.getSaveFileName(self, "Save download as", str(default))
        if not path:
            download.cancel()
            self.status.setText("Download cancelled.")
            return
        target = Path(path)
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.isFinishedChanged.connect(lambda d=download, t=target: self._download_finished(d, t))
        download.accept()
        self.downloads.append(download)
        self.status.setText(f"Downloading {target.name}…")

    def _download_finished(self, download, target: Path):
        state = download.state().name if hasattr(download.state(), "name") else str(download.state())
        self.status.setText(f"Downloaded {target}." if "Completed" in state else f"Download of {target.name}: {state}.")
