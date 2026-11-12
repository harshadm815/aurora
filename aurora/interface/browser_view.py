"""
The Browser page: the AURORA Browser inside the Command Centre, laid out
like a desktop browser.

    navigation bar   ☰ sidebar · back, forward, reload, home · security
                     indicator (click: site information and every site the
                     page contacted) · address and search · bookmark ·
                     reader · zoom · downloads · ✦ AURORA panel · ⋯ menu
    tabs             up to BROWSER_MAX_TABS; right-click a tab to pin,
                     duplicate, reload or close it (or close the others);
                     Ctrl+Shift+T restores closed tabs; Ctrl+Shift+A
                     searches open tabs
    left sidebar     Bookmarks · History · Downloads · Reading list ·
                     Saved pages (shown by default; Ctrl+B)
    main area        the page, or reader mode; find bar (Ctrl+F); zoom
                     (Ctrl + / - / 0); full screen (F11)
    AURORA panel     hidden until you open it (✦, or Ctrl+J): ask about the
                     page, key sentences, important information, compare
                     open tabs, explain the selected text (in Chat), save to
                     Knowledge, add to the reading list

Privacy and control (see aurora/browser/network.py):
- Chromium starts only when you first open this page.
- Every request goes through the network gate. With internet access on
  "ask", the first page each session asks you first.
- The security indicator lists every site the open page loaded something
  from; System -> Network activity lists them too.
- Sites can't use the camera, microphone, location or notifications.
- Downloads happen only after you confirm and choose where to save.
- History is on this PC only, can be switched off, and is cleared after
  BROWSER_HISTORY_DAYS days. Cookies and cache stay in AURORA's own
  browser profile.
"""

import html
import logging
from pathlib import Path

from PySide6.QtCore import QStringListModel, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QCompleter, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter, QStackedWidget, QTabWidget, QTextBrowser,
    QToolButton, QVBoxLayout, QWidget,
)

from aurora.browser import addresses, answers, extract, reader, saving
from aurora.browser.network import COMPONENT, BrowserNetwork, is_other_site
from aurora.browser.store import BrowserStore
from aurora.config import preferences, settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import label
from aurora.network import gate as network_gate

logger = logging.getLogger(__name__)

ZOOM_STEPS = (0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)
MAX_CLOSED = 10
PINNED = "pinned"

START_PAGE = """<html><head><title>New tab</title><style>
body{font-family:Segoe UI,sans-serif;background:#0b1120;color:#cbd5e1;margin:12% auto;max-width:560px;text-align:center}
h1{color:#67e8f9;font-weight:600;letter-spacing:3px} p{line-height:1.6;color:#94a3b8}</style></head><body>
<h1>AURORA BROWSER</h1><p>Type an address or a search in the bar above.</p>
<p>Pages load only when you open them. The padlock lists every site a page contacts.
Open the ✦ AURORA panel to ask about a page, pick out its key sentences, or compare tabs.</p></body></html>"""


def _qt_web_factory(network):
    from aurora.interface.browser_web import WebFactory      # starts Qt WebEngine only now
    return WebFactory(network)


def security(url: str) -> tuple[str, str]:
    """(indicator, tooltip) for an address."""
    if url.startswith("https://"):
        return "🔒", "Secure connection (HTTPS): what you send and receive is encrypted."
    if url.startswith("http://"):
        host = addresses.host_of(url)
        local = host in ("localhost", "127.0.0.1") or host.startswith(("192.168.", "10."))
        return ("🏠" if local else "⚠"), ("A device on your own network or this PC (not encrypted)." if local else
                                          "Not secure: this page isn't encrypted. Don't type passwords here.")
    return "", ""


def zoom_step(current: float, direction: int) -> float:
    """The next zoom level up (+1) or down (-1); 0 resets to 100 %."""
    if direction == 0:
        return 1.0
    if direction > 0:
        return next((z for z in ZOOM_STEPS if z > current + 1e-6), ZOOM_STEPS[-1])
    return next((z for z in reversed(ZOOM_STEPS) if z < current - 1e-6), ZOOM_STEPS[0])


def _tool(text: str, tip: str, handler, checkable: bool = False) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tip)
    button.setObjectName("NavTool")
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    if checkable:
        button.toggled.connect(handler)
    else:
        button.clicked.connect(handler)
    return button


class BrowserView(BaseView):
    title = "Browser"
    subtitle = ""
    nav_icon = "🌐"
    scrollable = False

    ask_chat = Signal(str)          # "Explain selection": answered in Chat by the language model

    def __init__(self, backend, parent=None, factory=None, store=None, network=None):
        super().__init__(backend, parent)
        self.body.itemAt(0).widget().hide()           # every pixel for the page
        self.body.setContentsMargins(10, 8, 10, 6)
        self.body.setSpacing(6)
        self.store = store or BrowserStore()
        self.network = network or BrowserNetwork()
        self.network.on_ask = lambda host: self.backend.on_gui(self._ask_later, host)
        self._factory_maker = factory or _qt_web_factory
        self.factory = None
        self._asking = False
        self._declined = False
        self.downloads: list = []
        self.closed: list[str] = []              # recently closed tabs' addresses
        self.fullscreen = False
        try:
            removed = self.store.prune()
            if removed:
                logger.info("Browser history: %d old visit(s) deleted", removed)
        except Exception:
            logger.exception("Pruning browser history failed")

        self.body.addLayout(self._navigation())

        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.sidebar = self._sidebar()
        self.split.addWidget(self.sidebar)
        self.split.addWidget(self._main_area())
        self.panel = self._aurora_panel()
        self.split.addWidget(self.panel)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setStretchFactor(2, 0)
        self.split.setSizes([230, 900, 340])
        self.panel.hide()                             # hidden until you ask for it
        self.body.addWidget(self.split, 1)
        self.status = label("", "Faint", wrap=True)
        self.body.addWidget(self.status)
        self._shortcuts()

    # ------------------------------------------------------------------ layout

    def _navigation(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(2)
        self.sidebar_button = _tool("☰", "Sidebar: bookmarks, history, downloads, reading list (Ctrl+B)",
                                    self.set_sidebar, checkable=True)
        self.sidebar_button.blockSignals(True)        # the sidebar is made after the bar
        self.sidebar_button.setChecked(True)
        self.sidebar_button.blockSignals(False)
        row.addWidget(self.sidebar_button)
        for text, tip, handler in (("◀", "Back (Alt+Left)", lambda: self._current_call("back")),
                                   ("▶", "Forward (Alt+Right)", lambda: self._current_call("forward")),
                                   ("⟳", "Reload (F5)", lambda: self._current_call("reload")),
                                   ("⌂", "Start page", self.home)):
            row.addWidget(_tool(text, tip, handler))
        self.address_frame = QFrame()
        self.address_frame.setObjectName("AddressBar")
        inner = QHBoxLayout(self.address_frame)
        inner.setContentsMargins(8, 0, 4, 0)
        inner.setSpacing(4)
        self.lock = QPushButton("")
        self.lock.setObjectName("Lock")
        self.lock.setFlat(True)
        self.lock.setCursor(Qt.PointingHandCursor)
        self.lock.clicked.connect(self.site_information)
        inner.addWidget(self.lock)
        self.address = QLineEdit()
        self.address.setObjectName("Address")
        self.address.setPlaceholderText("Search or type an address")
        self.address.returnPressed.connect(lambda: self.navigate(self.address.text()))
        self.suggestions = QStringListModel(self)
        completer = QCompleter(self.suggestions, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.activated.connect(lambda text: self.navigate(text.split("  —  ")[0]))
        self.address.setCompleter(completer)
        self.address.textEdited.connect(self._suggest)
        inner.addWidget(self.address, 1)
        self.star = _tool("☆", "Bookmark this page (Ctrl+D)", self.toggle_bookmark)
        inner.addWidget(self.star)
        row.addWidget(self.address_frame, 1)
        self.reader_button = _tool("📖", "Reader mode: the article without menus and adverts", self.toggle_reader,
                                   checkable=True)
        row.addWidget(self.reader_button)
        row.addWidget(_tool("−", "Zoom out (Ctrl+-)", lambda: self.zoom(-1)))
        self.zoom_label = QPushButton("100%")
        self.zoom_label.setObjectName("ZoomLabel")
        self.zoom_label.setFlat(True)
        self.zoom_label.setToolTip("Reset zoom (Ctrl+0)")
        self.zoom_label.clicked.connect(lambda: self.zoom(0))
        row.addWidget(self.zoom_label)
        row.addWidget(_tool("+", "Zoom in (Ctrl+=)", lambda: self.zoom(1)))
        row.addWidget(_tool("⬇", "Downloads", lambda: self.show_sidebar("Downloads")))
        self.panel_button = _tool("✦ AURORA", "AURORA panel: ask about the page, key sentences, compare tabs (Ctrl+J)",
                                  self.set_panel, checkable=True)
        self.panel_button.setObjectName("PanelToggle")
        row.addWidget(self.panel_button)
        menu_button = _tool("⋯", "Browser menu", lambda: None)
        menu_button.setPopupMode(QToolButton.InstantPopup)
        menu_button.setMenu(self._menu())
        row.addWidget(menu_button)
        return row

    def _menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("New tab\tCtrl+T", lambda: self.new_tab())
        menu.addAction("Restore closed tab\tCtrl+Shift+T", self.restore_tab)
        menu.addAction("Search open tabs…\tCtrl+Shift+A", self.search_tabs)
        menu.addSeparator()
        menu.addAction("Find on page…\tCtrl+F", self.show_find)
        menu.addAction("Full screen\tF11", self.toggle_fullscreen)
        menu.addSeparator()
        menu.addAction("Site information and sites contacted", self.site_information)
        self.history_action = menu.addAction("Keep history", self.set_history)
        self.history_action.setCheckable(True)
        self.history_action.setChecked(bool(settings.BROWSER_HISTORY))
        menu.addAction("Clear history…", self.clear_history)
        menu.addAction("Clear cookies and cache…", self.clear_site_data)
        menu.addSeparator()
        menu.addAction("Internet access settings", lambda: self._open_page("Settings"))
        return menu

    def _sidebar(self) -> QTabWidget:
        side = QTabWidget()
        side.setObjectName("BrowserSidebar")
        side.setMinimumWidth(200)
        side.setMaximumWidth(360)
        side.addTab(self._bookmarks_tab(), "★")
        side.addTab(self._history_tab(), "🕘")
        side.addTab(self._downloads_tab(), "⬇")
        side.addTab(self._reading_tab(), "📑")
        side.addTab(self._saved_tab(), "💾")
        for index, tip in enumerate(("Bookmarks", "History", "Downloads", "Reading list", "Saved pages")):
            side.setTabToolTip(index, tip)
        side.currentChanged.connect(lambda _: self._side_shown())
        return side

    def _list_tab(self, heading: str, list_widget: QListWidget, buttons=(), note: str = "") -> QWidget:
        tab, layout = QWidget(), QVBoxLayout()
        layout.setContentsMargins(4, 6, 4, 4)
        tab.setLayout(layout)
        layout.addWidget(label(heading.upper(), "SectionTitle"))
        layout.addWidget(list_widget, 1)
        if buttons:
            row = QHBoxLayout()
            for text, handler in buttons:
                button = QPushButton(text)
                button.clicked.connect(handler)
                row.addWidget(button)
            layout.addLayout(row)
        if note:
            layout.addWidget(label(note, "Faint", wrap=True))
        return tab

    def _bookmarks_tab(self) -> QWidget:
        self.bookmark_list = QListWidget()
        self.bookmark_list.itemDoubleClicked.connect(lambda item: self.navigate(item.data(256).url))
        return self._list_tab("Bookmarks", self.bookmark_list, (("Remove", self.remove_bookmark),))

    def _history_tab(self) -> QWidget:
        self.history_list = QListWidget()
        self.history_list.itemDoubleClicked.connect(lambda item: self.navigate(item.data(256).url))
        tab = self._list_tab("History", self.history_list, (("Delete", self.delete_visit),
                                                            ("Clear all", self.clear_history)),
                             f"On this PC only; deleted after {settings.BROWSER_HISTORY_DAYS} days.")
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("Search history")
        self.history_search.textChanged.connect(lambda _: self.load_history())
        self.keep_history = QCheckBox("Keep history")
        self.keep_history.setChecked(bool(settings.BROWSER_HISTORY))
        self.keep_history.toggled.connect(self.set_history)
        tab.layout().insertWidget(1, self.history_search)
        tab.layout().insertWidget(1, self.keep_history)
        return tab

    def _downloads_tab(self) -> QWidget:
        self.download_list = QListWidget()
        self.download_list.itemDoubleClicked.connect(self.open_download_folder)
        return self._list_tab("Downloads", self.download_list,
                              (("Show in folder", lambda: self.open_download_folder(self.download_list.currentItem())),
                               ("Clear list", self.clear_downloads)),
                              "Clearing the list doesn't delete the files.")

    def _reading_tab(self) -> QWidget:
        self.reading_list = QListWidget()
        self.reading_list.itemDoubleClicked.connect(self.open_reading_item)
        return self._list_tab("Reading list", self.reading_list,
                              (("Add this page", self.add_to_reading_list), ("Mark read", self.mark_read),
                               ("Remove", self.remove_reading_item)))

    def _saved_tab(self) -> QWidget:
        self.saved_list = QListWidget()
        self.saved_list.itemDoubleClicked.connect(self.open_saved_page)
        return self._list_tab("Saved pages", self.saved_list, (),
                              f"Pages saved to Knowledge (Documents/{settings.BROWSER_SAVED_FOLDER}). "
                              "Double-click to read one here.")

    def _main_area(self) -> QWidget:
        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.center = QStackedWidget()
        self.tabs = QTabWidget()
        self.tabs.setObjectName("BrowserTabs")
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        new = QToolButton()
        new.setText("+")
        new.setToolTip("New tab (Ctrl+T)")
        new.setAutoRaise(True)
        new.clicked.connect(lambda: self.new_tab())
        self.tabs.setCornerWidget(new, Qt.TopRightCorner)
        self.reader_view = QTextBrowser()
        self.reader_view.setObjectName("ReaderView")
        self.reader_view.setOpenLinks(False)
        self.reader_view.anchorClicked.connect(lambda url: self.navigate(url.toString()))
        self.center.addWidget(self.tabs)
        self.center.addWidget(self.reader_view)
        layout.addWidget(self.center, 1)

        self.find_bar = QWidget()
        find = QHBoxLayout(self.find_bar)
        find.setContentsMargins(0, 0, 0, 0)
        self.find_box = QLineEdit()
        self.find_box.setPlaceholderText("Find on page")
        self.find_box.returnPressed.connect(self.find)
        find.addWidget(self.find_box, 1)
        find.addWidget(_tool("▲", "Previous", lambda: self.find(backward=True)))
        find.addWidget(_tool("▼", "Next", self.find))
        find.addWidget(_tool("✕", "Close (Esc)", self.hide_find))
        self.find_bar.hide()
        layout.addWidget(self.find_bar)
        return area

    def _aurora_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("AuroraPanel")
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(460)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        head.addWidget(label("✦  AURORA", "PanelTitle"))
        head.addStretch(1)
        head.addWidget(_tool("✕", "Hide the panel (Ctrl+J)", lambda: self.panel_button.setChecked(False)))
        layout.addLayout(head)
        layout.addWidget(label("Answers are quoted from the page, so you can check them. Nothing is sent "
                               "anywhere.", "Faint", wrap=True))
        self.question = QLineEdit()
        self.question.setPlaceholderText("Ask about this page…")
        self.question.returnPressed.connect(self.ask)
        layout.addWidget(self.question)
        actions = (("Summarise", "Key sentences, quoted from the page", self.summarise),
                   ("Extract info", "Dates, figures, sections, tables and links", self.extract_info),
                   ("Compare tabs", "The open pages side by side", self.compare_tabs),
                   ("Explain selection", "Ask AURORA's language model in Chat about the selected text",
                    self.explain_selection),
                   ("Save to Knowledge", f"Save the text to Documents/{settings.BROWSER_SAVED_FOLDER}",
                    self.save_page),
                   ("Read later", "Add to the reading list", self.add_to_reading_list))
        grid = QVBoxLayout()
        grid.setSpacing(4)
        for start in range(0, len(actions), 2):
            row = QHBoxLayout()
            for text, tip, handler in actions[start:start + 2]:
                button = QPushButton(text)
                button.setToolTip(tip)
                button.clicked.connect(handler)
                row.addWidget(button)
            grid.addLayout(row)
        layout.addLayout(grid)
        self.answer_view = QTextBrowser()
        self.answer_view.setOpenLinks(False)
        self.answer_view.anchorClicked.connect(lambda url: self.navigate(url.toString()))
        layout.addWidget(self.answer_view, 1)
        return panel

    def _shortcuts(self):
        keys = {
            QKeySequence.Find: self.show_find, "Ctrl+T": lambda: self.new_tab(),
            "Ctrl+W": lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+Shift+T": self.restore_tab,
            "Ctrl+Shift+A": self.search_tabs, "Ctrl+L": lambda: (self.address.setFocus(), self.address.selectAll()),
            "Ctrl+D": self.toggle_bookmark, "Ctrl+B": lambda: self.sidebar_button.toggle(),
            "Ctrl+J": lambda: self.panel_button.toggle(), "Ctrl+=": lambda: self.zoom(1),
            "Ctrl++": lambda: self.zoom(1), "Ctrl+-": lambda: self.zoom(-1), "Ctrl+0": lambda: self.zoom(0),
            "F5": lambda: self._current_call("reload"), "F11": self.toggle_fullscreen,
            "Alt+Left": lambda: self._current_call("back"), "Alt+Right": lambda: self._current_call("forward"),
            "Escape": self._escape,
        }
        for key, handler in keys.items():
            QShortcut(QKeySequence(key), self, activated=handler)

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

    def on_hidden(self):
        if self.fullscreen:
            self.toggle_fullscreen()

    def new_tab(self, url: str | None = None, pinned: bool = False):
        """Opens a tab (start page, or url). Returns its view, or None at the tab limit."""
        if self.factory is None:
            return None
        if self.tabs.count() >= settings.BROWSER_MAX_TABS:
            self.status.setText(f"At most {settings.BROWSER_MAX_TABS} tabs (each uses 100-300 MB of memory). "
                                "Close one to open another.")
            return None
        view = self.factory.view(self.new_tab)
        view.setProperty(PINNED, pinned)
        view.titleChanged.connect(lambda title, v=view: self._title_changed(v, title))
        view.urlChanged.connect(lambda qurl, v=view: self._url_changed(v, qurl))
        view.loadFinished.connect(lambda ok, v=view: self._load_finished(v, ok))
        index = self.pinned_count() if pinned else self.tabs.count()
        index = self.tabs.insertTab(index, view, "New tab")
        self.tabs.setCurrentIndex(index)
        if pinned:
            self._style_tab(view)
        if url:
            self._load(view, url)
        else:
            view.setHtml(START_PAGE, QUrl("about:blank"))
            self.address.clear()
            self.address.setFocus()
        return view

    def pinned_count(self) -> int:
        return sum(1 for i in range(self.tabs.count()) if self.tabs.widget(i).property(PINNED))

    def close_tab(self, index: int):
        if index < 0 or index >= self.tabs.count():
            return
        view = self.tabs.widget(index)
        if view.property(PINNED):
            self.status.setText("Unpin the tab first (right-click it).")
            return
        url = view.url().toString()
        if addresses.is_web(url):
            self.closed.append(url)
            del self.closed[:-MAX_CLOSED]
        self.tabs.removeTab(index)
        view.deleteLater()

    def restore_tab(self):
        if not self.closed:
            self.status.setText("No closed tabs to restore.")
            return
        url = self.closed.pop()
        if self.new_tab(url) is None:
            self.closed.append(url)

    def duplicate_tab(self, index: int):
        view = self.tabs.widget(index)
        url = view.url().toString() if view is not None else ""
        if addresses.is_web(url):
            self.new_tab(url)

    def set_pinned(self, index: int, pinned: bool):
        view = self.tabs.widget(index)
        if view is None:
            return
        view.setProperty(PINNED, pinned)
        self.tabs.tabBar().moveTab(index, self.pinned_count() - 1 if pinned else self.pinned_count())
        self._style_tab(view)

    def _style_tab(self, view):
        index = self.tabs.indexOf(view)
        pinned = bool(view.property(PINNED))
        title = view.title() or "New tab"
        self.tabs.setTabText(index, ("📌 " + title[:6]) if pinned else title[:26])
        self.tabs.setTabToolTip(index, f"{title}\n{view.url().toString()}" + ("\n(pinned)" if pinned else ""))
        button = self.tabs.tabBar().tabButton(index, self.tabs.tabBar().ButtonPosition.RightSide)
        if button is not None:
            button.setVisible(not pinned)

    def close_other_tabs(self, keep: int):
        keep_view = self.tabs.widget(keep)
        for index in reversed(range(self.tabs.count())):
            view = self.tabs.widget(index)
            if view is not keep_view and not view.property(PINNED):
                self.close_tab(index)

    def _tab_menu(self, point):
        index = self.tabs.tabBar().tabAt(point)
        if index < 0:
            return
        view = self.tabs.widget(index)
        menu = QMenu(self)
        pinned = bool(view.property(PINNED))
        menu.addAction("Unpin tab" if pinned else "Pin tab", lambda: self.set_pinned(index, not pinned))
        menu.addAction("Duplicate tab", lambda: self.duplicate_tab(index))
        menu.addAction("Reload", view.reload)
        menu.addSeparator()
        close = menu.addAction("Close tab", lambda: self.close_tab(index))
        close.setEnabled(not pinned)
        menu.addAction("Close other tabs", lambda: self.close_other_tabs(index))
        menu.exec(self.tabs.tabBar().mapToGlobal(point))

    def search_tabs(self):
        if self.tabs.count() == 0:
            return
        titles = [f"{i + 1}. {self.tabs.widget(i).title() or 'New tab'} — {addresses.host_of(self.tabs.widget(i).url().toString())}"
                  for i in range(self.tabs.count())]
        choice, ok = QInputDialog.getItem(self, "Search open tabs", "Go to tab:", titles, self.tabs.currentIndex(),
                                          True)
        if ok and choice in titles:
            self.tabs.setCurrentIndex(titles.index(choice))

    def current(self):
        return self.tabs.currentWidget()

    def _current_call(self, method: str):
        view = self.current()
        if view is not None:
            getattr(view, method)()

    def home(self):
        view = self.current() or self.new_tab()
        if view is not None:
            view.setHtml(START_PAGE, QUrl("about:blank"))
            self.address.clear()
            self._update_bar()

    def navigate(self, text: str):
        """Opens what was typed in the current tab (a new one if there is none)."""
        try:
            url = addresses.resolve(text)
        except addresses.RefusedAddress as refused:
            self.status.setText(str(refused))
            return
        view = self.current() or self.new_tab()
        if view is not None:
            self.reader_button.setChecked(False)
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
                "the padlock lists every site contacted.\n\n"
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
        if self.tabs.indexOf(view) >= 0:
            self._style_tab(view)
        url = view.url().toString()
        if addresses.is_web(url):
            self.store.record_visit(url, title)

    def _url_changed(self, view, qurl):
        if view is self.current():
            self._update_bar()

    def _load_finished(self, view, ok: bool):
        url = view.url().toString()
        if addresses.is_web(url):
            self.store.record_visit(url, view.title())
        if view is self.current():
            self._update_bar()
            if not ok and addresses.is_web(url):
                blocked = [host for host, allowed in self.network.contacted(url) if not allowed]
                self.status.setText("The page didn't load" + (f" (blocked: {', '.join(blocked[:3])})" if blocked
                                                             else "") + ".")
            elif ok:
                self.status.setText("")

    def _tab_changed(self, _index: int):
        self.reader_button.setChecked(False)
        self._update_bar()

    def _update_bar(self):
        view = self.current()
        url = view.url().toString() if view is not None else ""
        self.address.setText("" if url in ("", addresses.BLANK) else url)
        icon, tip = security(url)
        self.lock.setText(icon)
        self.lock.setToolTip(tip + ("\nClick for the sites this page contacted." if icon else ""))
        self.lock.setVisible(bool(icon))
        marked = addresses.is_web(url) and self.store.bookmark_for(url) is not None
        self.star.setText("★" if marked else "☆")
        factor = view.zoomFactor() if view is not None and hasattr(view, "zoomFactor") else 1.0
        self.zoom_label.setText(f"{factor * 100:.0f}%")

    def _suggest(self, typed: str):
        self.suggestions.setStringList([f"{url}  —  {title}" for url, title in self.store.suggestions(typed)])

    # ------------------------------------------------------------ main area tools

    def zoom(self, direction: int):
        view = self.current()
        if view is None:
            return
        view.setZoomFactor(zoom_step(view.zoomFactor(), direction))
        self._update_bar()

    def show_find(self):
        self.find_bar.show()
        self.find_box.setFocus()
        self.find_box.selectAll()

    def hide_find(self):
        view = self.current()
        if view is not None:
            view.findText("")
        self.find_bar.hide()

    def find(self, backward: bool = False):
        view = self.current()
        if view is None:
            return
        if backward:
            from PySide6.QtWebEngineCore import QWebEnginePage
            view.findText(self.find_box.text(), QWebEnginePage.FindFlag.FindBackward)
        else:
            view.findText(self.find_box.text())

    def _escape(self):
        if self.fullscreen:
            self.toggle_fullscreen()
        elif self.find_bar.isVisible():
            self.hide_find()

    def toggle_fullscreen(self):
        window = self.window()
        self.fullscreen = not self.fullscreen
        app_sidebar = window.findChild(QFrame, "Sidebar") if window is not self else None
        top_bar = window.findChild(QFrame, "TopBar") if window is not self else None
        for widget in (app_sidebar, top_bar):
            if widget is not None:
                widget.setVisible(not self.fullscreen)
        self.sidebar.setVisible(not self.fullscreen and self.sidebar_button.isChecked())
        if window is not self:
            window.showFullScreen() if self.fullscreen else window.showNormal()
        self.status.setText("Full screen: F11 or Esc to leave." if self.fullscreen else "")

    def toggle_reader(self, on: bool):
        if on:
            self.reader_view.setPlainText("Reading the page…")
            self.center.setCurrentWidget(self.reader_view)
            self._with_page(self._show_reader, on_missing=lambda: self.reader_button.setChecked(False))
        else:
            self.center.setCurrentWidget(self.tabs)

    def show_reader(self):
        self.reader_button.setChecked(True)

    def _show_reader(self, page: reader.ReaderPage, _html: str = ""):
        if not page.blocks:
            self.reader_view.setPlainText("No article text found on this page (it may be mostly pictures, "
                                          "a PDF, or still loading).")
            return
        parts = ["<div style='max-width:720px;margin:auto;font-size:12pt;line-height:160%'>",
                 f"<h1>{html.escape(page.title)}</h1>",
                 f"<p style='color:#8b9bb4'>{html.escape(addresses.host_of(page.url) or page.url)} · "
                 f"{page.words} words · about {max(1, page.words // 230)} min read</p>"]
        for block in page.blocks:
            text = html.escape(block.text)
            if block.heading:
                parts.append(f"<h2>{text}</h2>")
            elif block.kind == "li":
                parts.append(f"<p>• {text}</p>")
            elif block.kind == "code":
                parts.append(f"<pre>{text}</pre>")
            elif block.kind == "quote":
                parts.append(f"<blockquote>{text}</blockquote>")
            else:
                parts.append(f"<p>{text}</p>")
        parts.append("</div>")
        self.reader_view.setHtml("".join(parts))

    # ---------------------------------------------------------- the AURORA panel

    def set_panel(self, on: bool):
        self.panel.setVisible(on)
        if on:
            self.question.setFocus()

    def set_sidebar(self, on: bool):
        self.sidebar.setVisible(on and not self.fullscreen)
        if on:
            self._side_shown()

    def show_sidebar(self, name: str):
        names = ["Bookmarks", "History", "Downloads", "Reading list", "Saved pages"]
        self.sidebar_button.setChecked(True)
        self.sidebar.setCurrentIndex(names.index(name))
        self._side_shown()

    def _with_page(self, then, on_missing=None, view=None):
        """Calls then(ReaderPage, html) with a tab's page text (read in the background)."""
        view = view or self.current()
        url = view.url().toString() if view is not None else ""
        if view is None or not addresses.is_web(url):
            self.status.setText("Open a web page first.")
            if on_missing is not None:
                on_missing()
            return

        def got_html(markup):
            self.backend.run_io(lambda: (reader.extract(markup or "", url), markup or ""),
                                lambda result: then(*result),
                                lambda error: self.status.setText(f"Reading the page didn't work: {error}"))

        view.page().toHtml(got_html)

    def _panel_text(self, text: str):
        self.panel_button.setChecked(True)
        self.answer_view.setPlainText(text)

    def ask(self):
        question = self.question.text().strip()
        if not question:
            return
        self._panel_text("Reading the page…")
        self._with_page(lambda page, _html: self.answer_view.setPlainText(answers.answer(page, question).text()))

    def summarise(self):
        self._panel_text("Reading the page…")
        self._with_page(lambda page, _html: self.answer_view.setPlainText(extract.summary(page).text()))

    def extract_info(self):
        self._panel_text("Reading the page…")
        self._with_page(lambda page, markup: self.answer_view.setPlainText(extract.details(page, markup).text()))

    def compare_tabs(self):
        views = [self.tabs.widget(i) for i in range(self.tabs.count())
                 if addresses.is_web(self.tabs.widget(i).url().toString())]
        if len(views) < 2:
            self._panel_text("Open at least two web pages in tabs to compare them.")
            return
        self._panel_text(f"Reading {len(views)} pages…")
        collected: list = []

        def got(page, markup):
            collected.append((page, markup))
            if len(collected) == len(views):
                order = {v.url().toString(): i for i, v in enumerate(views)}
                collected.sort(key=lambda item: order.get(item[0].url, 0))
                self.answer_view.setPlainText(extract.compare(collected).text())

        for view in views:
            self._with_page(got, view=view)

    def explain_selection(self):
        view = self.current()
        selected = view.selectedText().strip() if view is not None and hasattr(view, "selectedText") else ""
        if not selected:
            self._panel_text("Select some text on the page first, then press Explain selection.")
            return
        self.ask_chat.emit(f"Explain this, in simple words: “{selected[:1500]}”")

    def save_page(self):
        def save(page, _html):
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
            self.load_saved()
        self._with_page(save)

    # ------------------------------------------------------------- sidebar lists

    def _side_shown(self):
        if not self.sidebar.isVisible():
            return
        [self.load_bookmarks, self.load_history, self.load_downloads, self.load_reading, self.load_saved][
            self.sidebar.currentIndex()]()

    def load_history(self):
        self.history_list.clear()
        for visit in self.store.history(self.history_search.text()):
            item = QListWidgetItem(f"{visit.title}\n{visit.time:%d %b %H:%M} · {addresses.host_of(visit.url)}")
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
        for control in (self.keep_history, self.history_action):
            if control.isChecked() != keep:
                control.blockSignals(True)
                control.setChecked(keep)
                control.blockSignals(False)
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
            item = QListWidgetItem(f"{bookmark.title}\n{addresses.host_of(bookmark.url)}")
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
        self._update_bar()
        self.load_bookmarks()

    def remove_bookmark(self):
        item = self.bookmark_list.currentItem()
        if item is not None:
            self.store.remove_bookmark(item.data(256).id)
            self.load_bookmarks()
            self._update_bar()

    def load_downloads(self):
        self.download_list.clear()
        for download in self.store.downloads():
            item = QListWidgetItem(f"{download.name}\n{download.state} · {download.host} · {download.time:%d %b %H:%M}")
            item.setToolTip(download.path)
            item.setData(256, download)
            self.download_list.addItem(item)

    def open_download_folder(self, item):
        if item is None:
            return
        folder = Path(item.data(256).path).parent
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def clear_downloads(self):
        self.store.clear_downloads()
        self.load_downloads()

    def load_reading(self):
        self.reading_list.clear()
        for entry in self.store.reading_list():
            item = QListWidgetItem(("✓ " if entry.read else "") + f"{entry.title}\n{addresses.host_of(entry.url)}")
            item.setToolTip(entry.url)
            item.setData(256, entry)
            self.reading_list.addItem(item)

    def add_to_reading_list(self):
        view = self.current()
        url = view.url().toString() if view is not None else ""
        if not addresses.is_web(url):
            self.status.setText("Open a web page first.")
            return
        added = self.store.add_to_reading_list(url, view.title())
        self.status.setText("Added to your reading list." if added else "Already on your reading list.")
        self.load_reading()

    def open_reading_item(self, item):
        entry = item.data(256)
        self.store.mark_read(entry.id)
        self.navigate(entry.url)
        self.load_reading()

    def mark_read(self):
        item = self.reading_list.currentItem()
        if item is not None:
            self.store.mark_read(item.data(256).id, not item.data(256).read)
            self.load_reading()

    def remove_reading_item(self):
        item = self.reading_list.currentItem()
        if item is not None:
            self.store.remove_from_reading_list(item.data(256).id)
            self.load_reading()

    def load_saved(self):
        self.saved_list.clear()
        folder = saving.folder()
        files = sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
        for path in files:
            item = QListWidgetItem(path.stem)
            item.setToolTip(str(path))
            item.setData(256, path)
            self.saved_list.addItem(item)

    def open_saved_page(self, item):
        path = item.data(256)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            self.status.setText(f"Couldn't open it: {error}")
            return
        self.reader_button.blockSignals(True)
        self.reader_button.setChecked(True)
        self.reader_button.blockSignals(False)
        self.center.setCurrentWidget(self.reader_view)
        self.reader_view.setMarkdown(text)

    # -------------------------------------------------- site information and data

    def load_sites(self) -> list[str]:
        view = self.current()
        url = view.url().toString() if view is not None else ""
        if not addresses.is_web(url):
            return []
        return [f"{host}{'  · other site' if is_other_site(host, url) else ''}{'' if allowed else '  (blocked)'}"
                for host, allowed in self.network.contacted(url)]

    def site_information(self):
        view = self.current()
        url = view.url().toString() if view is not None else ""
        if not addresses.is_web(url):
            return
        _, tip = security(url)
        sites = self.load_sites()
        QMessageBox.information(self, "Site information",
                                f"{addresses.host_of(url)}\n{tip}\n\nSites this page contacted ({len(sites)}):\n"
                                + "\n".join(sites[:40]) + ("\n…" if len(sites) > 40 else "")
                                + "\n\nOther sites are often adverts, analytics or fonts. Blocked ones were "
                                  "stopped by the network settings.")

    def clear_site_data(self):
        if self.factory is None:
            return
        if QMessageBox.question(self, "Clear cookies and cache",
                                "Delete the AURORA Browser's cookies, cache and site data? You'll be signed out "
                                "of sites in this browser (not in your other browsers).") == QMessageBox.Yes:
            self.factory.clear_site_data()
            self.status.setText("Cookies and cache cleared.")

    def _open_page(self, name: str):
        window = self.window()
        if hasattr(window, "open_page"):
            window.open_page(name)

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
        record = self.store.record_download(target.name, str(target), host)
        download.isFinishedChanged.connect(lambda d=download, t=target, r=record: self._download_finished(d, t, r))
        download.accept()
        self.downloads.append(download)
        self.status.setText(f"Downloading {target.name}…")
        self.load_downloads()

    def _download_finished(self, download, target: Path, record: int):
        state = download.state().name if hasattr(download.state(), "name") else str(download.state())
        outcome = ("completed" if "Completed" in state else "cancelled" if "Cancelled" in state
                   else "failed" if "Interrupted" in state else state.lower())
        self.store.update_download(record, outcome)
        self.status.setText(f"Downloaded {target}." if outcome == "completed" else
                            f"Download of {target.name}: {outcome}.")
        self.load_downloads()
