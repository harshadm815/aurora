"""
The Documents page: AURORA's file manager, laid out like Windows File
Explorer.

    left         Home · Recent · Favourites, then the places (AURORA
                 Documents, your Documents, Pictures, Videos, Music,
                 Downloads, Desktop, OneDrive)
    toolbar      New folder · New document · Cut · Copy · Paste · Rename ·
                 Delete · Sort · view (Large icons / Details / List)
    breadcrumbs  the path inside the place, each part clickable; Up; a box
                 that filters names, or with Smart search finds documents
                 by what they say (AURORA's Documents folder only)
    centre       the folder (Qt's file system model, three views)
    right        preview (pictures, the start of text and PDFs), details,
                 Open · Open with… · Show in Explorer · Favourite ·
                 Summarise with AURORA

File operations follow aurora/explorer/operations.py: only inside the
places, never overwriting, delete to the Recycle Bin. AURORA reads file
contents (Smart search, summaries) only in its own Documents folder.
Keyboard: Enter open, F2 rename, Del delete, Ctrl+C / X / V, Ctrl+Shift+N
new folder, Backspace or Alt+Up up a level, Ctrl+F search, F5 refresh.
"""

import logging
import os
from pathlib import Path

from PySide6.QtCore import QDir, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileSystemModel, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
    QStackedWidget, QTextBrowser, QToolButton, QTreeView, QVBoxLayout, QWidget,
)

from aurora.config import settings
from aurora.explorer import operations
from aurora.explorer.operations import ExplorerError
from aurora.explorer.places import AURORA, is_aurora_document, place_of, places
from aurora.explorer.store import ExplorerStore
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import clear_layout, label

logger = logging.getLogger(__name__)

HOME, RECENT, FAVOURITES = "Home", "Recent", "Favourites"
ICONS, DETAILS, LIST = "Large icons", "Details", "List"
SORTS = [("Name", 0), ("Date modified", 3), ("Type", 2), ("Size", 1)]
SUMMARISABLE = {".txt", ".md", ".pdf"}


def _button(text: str, tip: str, handler) -> QToolButton:
    button = QToolButton()
    button.setText(text)
    button.setToolTip(tip)
    button.setObjectName("NavTool")
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonTextOnly)
    button.clicked.connect(handler)
    return button


class DocumentsView(BaseView):
    title = "Documents"
    subtitle = ""
    nav_icon = "📁"
    scrollable = False

    def __init__(self, backend, parent=None, store=None, recycle=None):
        super().__init__(backend, parent)
        self.body.itemAt(0).widget().hide()
        self.body.setContentsMargins(10, 8, 10, 6)
        self.body.setSpacing(6)
        self.store = store or ExplorerStore()
        self._recycle = recycle                     # tests pass a fake; None = the real Recycle Bin
        self.places = places()
        self.folder: Path | None = None
        self.special: str | None = HOME              # Home / Recent / Favourites / search results
        self.clipboard: tuple[list[Path], bool] = ([], False)    # (paths, cut)
        self.mode = DETAILS

        self.body.addLayout(self._toolbar())
        self.body.addLayout(self._address_row())

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(self._navigation())
        split.addWidget(self._centre())
        split.addWidget(self._preview_panel())
        split.setStretchFactor(1, 1)
        split.setSizes([210, 760, 300])
        self.body.addWidget(split, 1)
        self.status = label("", "Faint", wrap=True)
        self.body.addWidget(self.status)
        self._shortcuts()
        self.show_home()

    # ------------------------------------------------------------------ layout

    def _toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(2)
        for text, tip, handler in (
                ("📁＋ New folder", "New folder (Ctrl+Shift+N)", self.new_folder),
                ("📝 New document", "New Markdown document", self.new_document),
                ("✂ Cut", "Cut (Ctrl+X)", lambda: self.copy(cut=True)),
                ("⧉ Copy", "Copy (Ctrl+C)", lambda: self.copy(cut=False)),
                ("📋 Paste", "Paste (Ctrl+V)", self.paste),
                ("✎ Rename", "Rename (F2)", self.rename),
                ("🗑 Delete", "Move to the Recycle Bin (Del)", self.delete)):
            row.addWidget(_button(text, tip, handler))
        row.addStretch(1)
        row.addWidget(label("Sort:", "Faint"))
        self.sort_box = QComboBox()
        for name, column in SORTS:
            self.sort_box.addItem(name, column)
        self.sort_box.currentIndexChanged.connect(self._sort)
        row.addWidget(self.sort_box)
        self.view_box = QComboBox()
        self.view_box.addItems([DETAILS, ICONS, LIST])
        self.view_box.currentTextChanged.connect(self.set_mode)
        row.addWidget(self.view_box)
        return row

    def _address_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(_button("↑", "Up a level (Alt+Up)", self.up))
        self.crumbs_frame = QFrame()
        self.crumbs_frame.setObjectName("AddressBar")
        self.crumbs = QHBoxLayout(self.crumbs_frame)
        self.crumbs.setContentsMargins(8, 0, 8, 0)
        self.crumbs.setSpacing(0)
        row.addWidget(self.crumbs_frame, 1)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search this folder")
        self.search_box.setMinimumWidth(240)
        self.search_box.returnPressed.connect(self.search)
        self.search_box.textChanged.connect(self._filter)
        row.addWidget(self.search_box)
        self.smart = QCheckBox("Smart search")
        self.smart.setToolTip("Find documents by what they say (AURORA's Documents folder: .txt, .md, .pdf)")
        self.smart.toggled.connect(lambda on: self.search_box.setPlaceholderText(
            "Find documents that mention… (press Enter)" if on else "Search this folder"))
        row.addWidget(self.smart)
        return row

    def _navigation(self) -> QListWidget:
        self.nav = QListWidget()
        self.nav.setObjectName("ExplorerNav")
        self.nav.setMinimumWidth(170)
        self.nav.setMaximumWidth(260)
        for name, icon in ((HOME, "⌂"), (RECENT, "🕘"), (FAVOURITES, "★")):
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(256, name)
            self.nav.addItem(item)
        divider = QListWidgetItem("")
        divider.setFlags(Qt.NoItemFlags)
        divider.setSizeHint(QSize(10, 10))
        self.nav.addItem(divider)
        for place in self.places:
            item = QListWidgetItem(f"{place.icon}  {place.name}")
            item.setData(256, place)
            item.setToolTip(str(place.path) + ("\nAURORA indexes, searches and summarises these files."
                                               if place.kind == AURORA else ""))
            self.nav.addItem(item)
        self.nav.itemClicked.connect(self._nav_clicked)
        return self.nav

    def _centre(self) -> QWidget:
        self.model = QFileSystemModel(self)
        self.model.setReadOnly(True)                         # all changes go through operations.py
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs)
        self.model.setNameFilterDisables(False)
        self.stack = QStackedWidget()

        self.details = QTreeView()
        self.details.setRootIsDecorated(False)
        self.details.setItemsExpandable(False)
        self.details.setSortingEnabled(True)
        self.details.setAlternatingRowColors(True)
        self.icons = QListView()
        self.icons.setViewMode(QListView.IconMode)
        self.icons.setIconSize(QSize(64, 64))
        self.icons.setGridSize(QSize(112, 104))
        self.icons.setResizeMode(QListView.Adjust)
        self.icons.setWordWrap(True)
        self.icons.setMovement(QListView.Static)
        self.list = QListView()
        self.list.setViewMode(QListView.ListMode)
        self.list.setFlow(QListView.TopToBottom)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListView.Adjust)
        for view in (self.details, self.icons, self.list):
            view.setModel(self.model)
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
            view.setEditTriggers(QAbstractItemView.NoEditTriggers)
            view.setContextMenuPolicy(Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(lambda point, v=view: self._context_menu(v, point))
            view.doubleClicked.connect(lambda index: self.open_path(Path(self.model.filePath(index))))
            view.selectionModel().selectionChanged.connect(lambda *_: self._selected_changed())
            self.stack.addWidget(view)
        header = self.details.header()
        header.resizeSection(0, 320)
        header.resizeSection(1, 90)
        header.resizeSection(2, 140)

        self.results = QListWidget()                          # Home, Recent, Favourites, search results
        self.results.setObjectName("ExplorerResults")
        self.results.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.results.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results.customContextMenuRequested.connect(lambda point: self._context_menu(self.results, point))
        self.results.itemDoubleClicked.connect(lambda item: self.open_path(item.data(256)))
        self.results.itemSelectionChanged.connect(self._selected_changed)
        self.stack.addWidget(self.results)
        return self.stack

    def _preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("AuroraPanel")
        panel.setMinimumWidth(260)
        panel.setMaximumWidth(420)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        self.preview_title = label("", "PanelTitle", wrap=True)
        layout.addWidget(self.preview_title)
        self.preview_image = QLabel()
        self.preview_image.setAlignment(Qt.AlignCenter)
        self.preview_image.setMinimumHeight(10)
        layout.addWidget(self.preview_image)
        self.preview_text = QTextBrowser()
        self.preview_text.setMaximumHeight(220)
        layout.addWidget(self.preview_text)
        self.facts = QFormLayout()
        self.facts.setLabelAlignment(Qt.AlignRight)
        layout.addLayout(self.facts)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open")
        self.open_button.setObjectName("Primary")
        self.open_button.clicked.connect(lambda: self.open_path(self.selected()[0]) if self.selected() else None)
        self.open_with_button = QPushButton("Open with…")
        self.open_with_button.clicked.connect(self.open_with)
        self.reveal_button = QPushButton("Show in Explorer")
        self.reveal_button.clicked.connect(self.reveal)
        for button in (self.open_button, self.open_with_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        more = QHBoxLayout()
        self.favourite_button = QPushButton("☆ Favourite")
        self.favourite_button.clicked.connect(self.toggle_favourite)
        more.addWidget(self.reveal_button)
        more.addWidget(self.favourite_button)
        layout.addLayout(more)
        self.summary_button = QPushButton("✦ Summarise with AURORA")
        self.summary_button.setToolTip("Key sentences quoted from the document (AURORA's Documents folder only)")
        self.summary_button.clicked.connect(self.summarise)
        layout.addWidget(self.summary_button)
        self.summary_view = QTextBrowser()
        layout.addWidget(self.summary_view, 1)
        self._show_preview(None)
        return panel

    def _shortcuts(self):
        # Keys that mean something while typing (Enter, Backspace, Ctrl+C…) act only on the file views.
        on_files = {"Return": self._open_selected, "F2": self.rename, "Delete": self.delete,
                    "Ctrl+C": lambda: self.copy(False), "Ctrl+X": lambda: self.copy(True), "Ctrl+V": self.paste,
                    "Backspace": self.up}
        for key, handler in on_files.items():
            shortcut = QShortcut(QKeySequence(key), self.stack, activated=handler)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        anywhere = {"Ctrl+Shift+N": self.new_folder, "Alt+Up": self.up, "F5": self.refresh,
                    "Ctrl+F": lambda: (self.search_box.setFocus(), self.search_box.selectAll())}
        for key, handler in anywhere.items():
            QShortcut(QKeySequence(key), self, activated=handler)

    # --------------------------------------------------------------- navigation

    def _nav_clicked(self, item):
        target = item.data(256)
        if target == HOME:
            self.show_home()
        elif target == RECENT:
            self.show_list(RECENT, self.store.recent(), "Files you opened or previewed here recently.")
        elif target == FAVOURITES:
            self.show_list(FAVOURITES, self.store.favourites(), "Right-click a file or folder → Add to favourites.")
        elif target is not None:
            self.open_folder(target.path)

    def open_folder(self, folder: Path):
        folder = Path(folder)
        if place_of(folder, self.places) is None:
            self.status.setText(f"{folder} isn't in one of the places shown here.")
            return
        if not folder.is_dir():
            if place_of(folder, self.places).kind == AURORA:
                folder.mkdir(parents=True, exist_ok=True)       # AURORA's own folder: create it on first use
            else:
                self.status.setText(f"{folder} doesn't exist.")
                return
        self.folder, self.special = folder, None
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self.model.setNameFilters([])
        root = self.model.setRootPath(str(folder))
        for view in (self.details, self.icons, self.list):
            view.setRootIndex(root)
        self._sort()
        self.set_mode(self.mode)
        self._breadcrumbs()
        self._show_preview(None)
        place = place_of(folder, self.places)
        self.status.setText(str(folder) + ("   ·   AURORA indexes and can summarise these files"
                                           if place.kind == AURORA else ""))

    def up(self):
        if self.folder is None:
            return
        place = place_of(self.folder, self.places)
        if place is not None and self.folder.resolve() != place.path.resolve():
            self.open_folder(self.folder.parent)

    def refresh(self):
        if self.special is None and self.folder is not None:
            self.open_folder(self.folder)
        elif self.special == HOME:
            self.show_home()

    def _breadcrumbs(self):
        clear_layout(self.crumbs)
        if self.special is not None:
            self.crumbs.addWidget(label(self.special, "Dim"))
            self.crumbs.addStretch(1)
            return
        place = place_of(self.folder, self.places)
        parts = [(place.name, place.path)]
        for part in self.folder.resolve().relative_to(place.path.resolve()).parts:
            parts.append((part, parts[-1][1] / part))
        for number, (name, path) in enumerate(parts):
            if number:
                self.crumbs.addWidget(label(" › ", "Faint"))
            crumb = QPushButton(name)
            crumb.setObjectName("Crumb")
            crumb.setFlat(True)
            crumb.setCursor(Qt.PointingHandCursor)
            crumb.clicked.connect(lambda _=False, p=path: self.open_folder(p))
            self.crumbs.addWidget(crumb)
        self.crumbs.addStretch(1)

    def set_mode(self, mode: str):
        self.mode = mode
        if self.view_box.currentText() != mode:
            self.view_box.setCurrentText(mode)
        if self.special is None:
            self.stack.setCurrentWidget({DETAILS: self.details, ICONS: self.icons, LIST: self.list}[mode])

    def _sort(self, *_):
        column = self.sort_box.currentData()
        order = Qt.DescendingOrder if column in (1, 3) else Qt.AscendingOrder     # newest and biggest first
        self.model.sort(column, order)
        self.details.sortByColumn(column, order)

    def _filter(self, text: str):
        if self.special is None and not self.smart.isChecked():
            self.model.setNameFilters([f"*{text.strip()}*"] if text.strip() else [])

    # ---------------------------------------------------------- special lists

    def show_list(self, title: str, paths: list[Path], empty: str = ""):
        self.special = title
        self.results.clear()
        for path in paths:
            self._add_result(path)
        if not paths and empty:
            item = QListWidgetItem(empty)
            item.setFlags(Qt.NoItemFlags)
            self.results.addItem(item)
        self.stack.setCurrentWidget(self.results)
        self._breadcrumbs()
        self._show_preview(None)

    def _add_result(self, path: Path, detail: str = ""):
        icon = "📁" if path.is_dir() else "📄"
        where = place_of(path, self.places)
        location = f"{where.name}/{path.relative_to(where.path).as_posix()}" if where else str(path)
        item = QListWidgetItem(f"{icon}  {path.name}\n      {location}" + (f"\n      “{detail}”" if detail else ""))
        item.setData(256, path)
        item.setToolTip(str(path))
        self.results.addItem(item)

    def show_home(self):
        self.special = HOME
        self.results.clear()
        sections = (("Favourites", self.store.favourites()[:8]), ("Recent", self.store.recent(10)))
        for heading, paths in sections:
            head = QListWidgetItem(heading.upper())
            head.setFlags(Qt.NoItemFlags)
            self.results.addItem(head)
            if not paths:
                none = QListWidgetItem("   Nothing yet.")
                none.setFlags(Qt.NoItemFlags)
                self.results.addItem(none)
            for path in paths:
                self._add_result(path)
        head = QListWidgetItem("PLACES")
        head.setFlags(Qt.NoItemFlags)
        self.results.addItem(head)
        for place in self.places:
            item = QListWidgetItem(f"{place.icon}  {place.name}\n      {place.path}")
            item.setData(256, place.path)
            self.results.addItem(item)
        self.stack.setCurrentWidget(self.results)
        self._breadcrumbs()
        self._show_preview(None)
        self.status.setText("Home: favourites, recent files and your places.")

    def search(self):
        text = self.search_box.text().strip()
        if not text:
            return
        if self.smart.isChecked():
            self.smart_search(text)
            return
        root = self.folder or Path(settings.DOCUMENTS_DIR)
        self.status.setText(f"Searching names in {root}…")
        self.backend.run_io(lambda: operations.search_names(root, text),
                            lambda found: (self.show_list(f"Search: “{text}” in {root.name}", found,
                                                          "Nothing with that in its name."),
                                           self.status.setText(f"{len(found)} found in {root}.")),
                            lambda error: self.status.setText(f"Search failed: {error}"))

    def smart_search(self, text: str):
        engine = self.backend.engine
        if engine is None or engine.tool_manager.get("Knowledge") is None:
            self.status.setText("Smart search needs the Knowledge tool.")
            return
        self.status.setText("Searching what your documents say…")

        def show(result):
            matches = result.data if isinstance(result.data, list) else []
            self.special = f"Smart search: “{text}”"
            self.results.clear()
            documents = Path(settings.DOCUMENTS_DIR)
            for name, page, passage in matches:
                path = documents / name
                if path.exists():
                    self._add_result(path, (f"p. {page}: " if page else "") + passage)
            if not matches:
                item = QListWidgetItem(result.message)
                item.setFlags(Qt.NoItemFlags)
                self.results.addItem(item)
            self.stack.setCurrentWidget(self.results)
            self._breadcrumbs()
            self.status.setText(result.message + "   (searched AURORA's Documents folder by content)")

        self.backend.run_io(lambda: engine.tool_manager.execute("Knowledge", action="find", question=text), show,
                            lambda error: self.status.setText(f"Smart search failed: {error}"))

    # -------------------------------------------------------------- selection

    def selected(self) -> list[Path]:
        if self.stack.currentWidget() is self.results:
            return [item.data(256) for item in self.results.selectedItems() if isinstance(item.data(256), Path)]
        view = self.stack.currentWidget()
        rows = view.selectionModel().selectedRows(0) if view is self.details else view.selectionModel().selectedIndexes()
        return list(dict.fromkeys(Path(self.model.filePath(index)) for index in rows))

    def _selected_changed(self):
        chosen = self.selected()
        self._show_preview(chosen[0] if len(chosen) == 1 else None, len(chosen))

    def _show_preview(self, path: Path | None, count: int = 0):
        clear_layout(self.facts)
        self.preview_image.clear()
        self.preview_image.setVisible(False)
        self.preview_text.clear()
        self.preview_text.setVisible(False)
        self.summary_view.clear()
        for button in (self.open_button, self.open_with_button, self.reveal_button, self.favourite_button,
                       self.summary_button):
            button.setEnabled(path is not None)
        if path is None:
            self.preview_title.setText(f"{count} items selected" if count > 1 else "Select a file to see it here")
            return
        try:
            details = operations.info(path)
        except OSError as error:
            self.preview_title.setText(f"{path.name}: {error.strerror or error}")
            return
        self.preview_title.setText(details.name)
        suffix = path.suffix.lower()
        if suffix in operations.IMAGE_PREVIEW and (details.size or 0) <= operations.PREVIEW_BYTES * 5:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self.preview_image.setPixmap(pixmap.scaled(260, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.preview_image.setVisible(True)
        elif not details.is_dir:
            self._preview_for = path

            def shown(text, wanted=path):
                if text and getattr(self, "_preview_for", None) == wanted:     # still the selected file
                    self.preview_text.setPlainText(text)
                    self.preview_text.setVisible(True)
            self.backend.run_io(lambda: operations.preview_text(path), shown, lambda _error: None)
        rows = [("Type", details.type), ("Size", operations.human_size(details.size)),
                ("Created", f"{details.created:%d %b %Y %H:%M}"), ("Modified", f"{details.modified:%d %b %Y %H:%M}"),
                ("Location", details.location)]
        for name, value in rows:
            if value:
                value_label = label(value, "Dim", wrap=True)
                value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                self.facts.addRow(label(name, "Faint"), value_label)
        aurora = is_aurora_document(path)
        self.summary_button.setEnabled(aurora and suffix in SUMMARISABLE)
        self.summary_button.setToolTip("Key sentences quoted from the document" if aurora else
                                       "AURORA reads only files in its own Documents folder. Copy it there to "
                                       "summarise it.")
        self.open_with_button.setEnabled(not details.is_dir)
        self.favourite_button.setText("★ Favourite" if self.store.is_favourite(path) else "☆ Favourite")
        if not details.is_dir:
            self.store.opened(path)

    # ------------------------------------------------------------- operations

    def _target_folder(self) -> Path | None:
        if self.special is not None or self.folder is None:
            self.status.setText("Open a folder first (one of the places on the left).")
            return None
        return self.folder

    def _run(self, action, success: str = ""):
        try:
            result = action()
        except ExplorerError as problem:
            self.status.setText(str(problem))
            return None
        except OSError as error:
            self.status.setText(f"That didn't work: {error.strerror or error}")
            return None
        if success:
            self.status.setText(success.format(result=result))
        return result

    def new_folder(self):
        folder = self._target_folder()
        if folder is None:
            return
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:", text="New folder")
        if ok:
            self._run(lambda: operations.new_folder(folder, name, self.places), "Created {result}.")

    def new_document(self):
        folder = self._target_folder()
        if folder is None:
            return
        name, ok = QInputDialog.getText(self, "New document", "Document name (.md, or type .txt):",
                                        text="New document.md")
        if ok:
            self._run(lambda: operations.new_document(folder, name, self.places), "Created {result}.")

    def copy(self, cut: bool):
        chosen = self.selected()
        if not chosen:
            return
        self.clipboard = (chosen, cut)
        self.status.setText(f"{'Cut' if cut else 'Copied'} {len(chosen)} item(s). Open a folder and paste.")

    def paste(self):
        folder = self._target_folder()
        sources, cut = self.clipboard
        if folder is None or not sources:
            return
        done = self._run(lambda: operations.paste(sources, folder, cut, self.places))
        if done is not None:
            if cut:
                for source in sources:
                    self.store.forget(source)
                self.clipboard = ([], False)
            self.status.setText(f"{'Moved' if cut else 'Copied'} {len(done)} item(s) to {folder.name}.")

    def rename(self):
        chosen = self.selected()
        if len(chosen) != 1:
            self.status.setText("Select one item to rename.")
            return
        path = chosen[0]
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=path.name)
        if ok and name != path.name:
            new = self._run(lambda: operations.rename(path, name, self.places), "Renamed to {result}.")
            if new is not None:
                self.store.forget(path)
                if self.special is not None:
                    self.refresh()

    def delete(self):
        chosen = self.selected()
        if not chosen:
            return
        names = ", ".join(p.name for p in chosen[:5]) + ("…" if len(chosen) > 5 else "")
        if QMessageBox.question(self, "Move to the Recycle Bin",
                                f"Move {len(chosen)} item(s) to the Recycle Bin?\n\n{names}\n\n"
                                "You can restore them from the Recycle Bin.") != QMessageBox.Yes:
            return
        kwargs = {"mover": self._recycle} if self._recycle is not None else {}
        moved = self._run(lambda: operations.to_recycle_bin(chosen, self.places, **kwargs))
        if moved is not None:
            for path in moved:
                self.store.forget(path)
            self.status.setText(f"Moved {len(moved)} item(s) to the Recycle Bin.")
            if self.special is not None:
                self.refresh()
            self._show_preview(None)

    def _open_selected(self):
        chosen = self.selected()
        if len(chosen) == 1:
            self.open_path(chosen[0])

    def open_path(self, path):
        if not isinstance(path, Path):
            return
        if path.is_dir():
            self.open_folder(path)
            return
        self.store.opened(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))         # Windows' default app, as Explorer does

    def open_with(self):
        chosen = self.selected()
        if len(chosen) == 1 and chosen[0].is_file() and hasattr(os, "startfile"):
            try:
                os.startfile(str(chosen[0]), "openas")                   # Windows' own "Open with" dialog
            except OSError as error:
                self.status.setText(f"Couldn't show Open with: {error}")

    def reveal(self):
        chosen = self.selected()
        if chosen:
            folder = chosen[0] if chosen[0].is_dir() else chosen[0].parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def toggle_favourite(self):
        chosen = self.selected()
        if len(chosen) == 1:
            on = not self.store.is_favourite(chosen[0])
            self.store.set_favourite(chosen[0], on)
            self.favourite_button.setText("★ Favourite" if on else "☆ Favourite")
            self.status.setText("Added to favourites." if on else "Removed from favourites.")

    def summarise(self):
        chosen = self.selected()
        engine = self.backend.engine
        if len(chosen) != 1 or engine is None:
            return
        path = chosen[0]
        if not is_aurora_document(path):
            self.summary_view.setPlainText("AURORA reads only files in its own Documents folder.")
            return
        name = path.resolve().relative_to(Path(settings.DOCUMENTS_DIR).resolve()).as_posix()
        self.summary_view.setPlainText("Reading the document…")
        self.backend.run_io(lambda: engine.tool_manager.execute("Knowledge", action="summarize", document=name),
                            lambda result: self.summary_view.setPlainText(result.message),
                            lambda error: self.summary_view.setPlainText(f"That didn't work: {error}"))

    def _context_menu(self, view, point):
        chosen = self.selected()
        menu = QMenu(self)
        if chosen:
            menu.addAction("Open", self._open_selected).setEnabled(len(chosen) == 1)
            menu.addAction("Open with…", self.open_with).setEnabled(len(chosen) == 1 and chosen[0].is_file())
            menu.addAction("Show in Explorer", self.reveal)
            menu.addSeparator()
            menu.addAction("Cut", lambda: self.copy(True))
            menu.addAction("Copy", lambda: self.copy(False))
        if self.special is None:
            paste = menu.addAction("Paste", self.paste)
            paste.setEnabled(bool(self.clipboard[0]))
        if chosen:
            menu.addAction("Rename", self.rename).setEnabled(len(chosen) == 1)
            menu.addAction("Delete (Recycle Bin)", self.delete)
            menu.addSeparator()
            if len(chosen) == 1:
                favourite = self.store.is_favourite(chosen[0])
                menu.addAction("Remove from favourites" if favourite else "Add to favourites", self.toggle_favourite)
                summary = menu.addAction("Summarise with AURORA", self.summarise)
                summary.setEnabled(is_aurora_document(chosen[0]) and chosen[0].suffix.lower() in SUMMARISABLE)
        if self.special is None:
            menu.addSeparator()
            menu.addAction("New folder", self.new_folder)
            menu.addAction("New document", self.new_document)
            menu.addAction("Refresh", self.refresh)
        menu.exec(view.viewport().mapToGlobal(point))
