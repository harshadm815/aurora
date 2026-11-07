"""Memory Centre: inspect, search, edit, pin and delete AURORA's memories,
and control what it keeps (learning, activity history, routines, block list)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import label
from aurora.knowledge.fact import EXPLICIT, Fact

COLUMNS = ["ID", "Category", "Attribute", "Value", "About", "Source", "Kept", "Confidence", "Priority",
           "Created", "Last used", "Used"]
ALL = "All categories"
SOURCES = {"automatic": "learned", "explicit": "you asked", "edited": "you edited", "unknown": "earlier"}


def kept(fact: Fact) -> str:
    if fact.pinned:
        return "📌 always"
    if fact.expires:
        return f"until {fact.expires:%d %b %H:%M}"
    return "until deleted"


class ListDialog(QDialog):
    """A list with one action button (delete / allow again) and Close."""

    def __init__(self, parent, title: str, intro: str, rows: list[tuple[str, object]],
                 action: str | None = None, on_action=None, extra: str | None = None, on_extra=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(label(intro, "Faint", wrap=True))
        self.list = QListWidget()
        for text, value in rows:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, value)
            self.list.addItem(item)
        if not rows:
            self.list.addItem("(nothing here)")
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        if action and rows:
            button = buttons.addButton(action, QDialogButtonBox.ActionRole)
            button.clicked.connect(lambda: self._act(on_action))
        if extra and rows:
            button = buttons.addButton(extra, QDialogButtonBox.DestructiveRole)
            button.setObjectName("Danger")
            button.clicked.connect(lambda: (on_extra(), self.accept()))
        layout.addWidget(buttons)

    def _act(self, on_action):
        item = self.list.currentItem()
        if item is None or item.data(Qt.UserRole) is None:
            return
        on_action(item.data(Qt.UserRole))
        self.list.takeItem(self.list.row(item))


class MemoryView(BaseView):
    title = "Memory Centre"
    subtitle = ("Everything AURORA remembers about you, from its SQLite memory. "
                "Edit, pin or delete anything, and choose what it keeps.")
    nav_icon = "🧠"
    scrollable = False

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.facts: list[Fact] = []

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search memories…")
        self.search.textChanged.connect(self._fill)
        self.category = QComboBox()
        self.category.addItem(ALL)
        self.category.currentTextChanged.connect(self._fill)
        tools.addWidget(self.search, 2)
        tools.addWidget(self.category, 1)
        for text, handler, name in [
            ("Add note", self._add, None), ("Edit value", self._edit, None), ("Pin / unpin", self._pin, None),
            ("How do you know?", self._why, None), ("Delete", self._delete, "Danger"),
            ("Delete && don't learn again", self._block, "Danger"), ("Refresh", self.refresh, None),
        ]:
            button = QPushButton(text)
            if name:
                button.setObjectName(name)
            button.clicked.connect(handler)
            tools.addWidget(button)
        self.body.addLayout(tools)

        controls = QHBoxLayout()
        self.learning = QCheckBox("Learn automatically from conversations")
        self.learning.toggled.connect(self._set_learning)
        self.activity = QCheckBox(f"Keep a short activity history ({settings.EPISODE_RETENTION_DAYS} days)")
        self.activity.toggled.connect(self._set_activity)
        controls.addWidget(self.learning)
        controls.addWidget(self.activity)
        controls.addStretch(1)
        for text, handler in [("Activity history…", self._show_activity), ("Routines…", self._show_routines),
                              ("Blocked…", self._show_blocked)]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            controls.addWidget(button)
        self.body.addLayout(controls)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda _: self._edit())
        self.body.addWidget(self.table, 1)

        self.summary = label("", "Faint")
        self.body.addWidget(self.summary)

    # --- Loading ------------------------------------------------------------------

    @property
    def engine(self):
        return self.backend.engine

    @property
    def memory(self):
        return self.engine.memory if self.engine is not None else None

    def on_engine_ready(self):
        self.refresh()

    def on_shown(self):
        self.refresh()

    def refresh(self):
        memory = self.memory
        if memory is None:
            self.summary.setText("AURORA is starting…")
            return
        engine = self.engine

        def load():
            return memory.memories(), memory.learning_paused, engine.episodes.enabled

        self.backend.run_io(load, self._loaded,
                            lambda error: self.summary.setText(f"Couldn't read memories: {error}"))

    def _loaded(self, loaded):
        facts, paused, activity = loaded
        for box, value in ((self.learning, not paused), (self.activity, activity)):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        self.facts = facts
        current = self.category.currentText()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem(ALL)
        self.category.addItems(sorted({f.category for f in facts}))
        self.category.setCurrentText(current if current in {f.category for f in facts} else ALL)
        self.category.blockSignals(False)
        self._fill()

    def _visible(self) -> list[Fact]:
        query = self.search.text().strip().lower()
        category = self.category.currentText()
        return [
            fact for fact in self.facts
            if (category == ALL or fact.category == category)
            and (not query or query in f"{fact.category} {fact.entity} {fact.attribute} {fact.value}".lower())
        ]

    def _fill(self):
        facts = self._visible()
        self.table.setRowCount(len(facts))
        for row, fact in enumerate(facts):
            values = [
                str(fact.id), fact.category, fact.attribute, fact.value,
                "you" if fact.entity == "User" else fact.entity,
                SOURCES.get(fact.source, fact.source), kept(fact),
                f"{fact.confidence:.2f}", str(fact.priority),
                fact.created.strftime("%d %b %Y %H:%M") if fact.created else "—",
                fact.last_used.strftime("%d %b %Y %H:%M") if fact.last_used else "never",
                str(fact.times_used),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 7, 8, 11):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
        self.summary.setText(f"Showing {len(facts)} of {len(self.facts)} memories."
                             + ("" if self.learning.isChecked() else "  Automatic learning is paused."))

    def _selected(self) -> Fact | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        memory_id = int(self.table.item(rows[0].row(), 0).text())
        return next((f for f in self.facts if f.id == memory_id), None)

    def _require(self, title: str) -> Fact | None:
        fact = self._selected()
        if fact is None:
            QMessageBox.information(self, title, "Select a memory first.")
        return fact

    # --- Changes (all through MemoryManager) ------------------------------------

    def _add(self):
        if self.memory is None:
            return
        text, ok = QInputDialog.getText(self, "Add a note", "What should AURORA remember?")
        text = text.strip()
        if ok and text:
            fact = Fact("Note", "User", "Note", text, 3)
            self.backend.run_io(lambda: self.memory.learn(fact, source=EXPLICIT, source_text=text),
                                lambda _: self.refresh(), self._failed)

    def _edit(self):
        fact = self._require("Edit memory")
        if fact is None:
            return
        value, ok = QInputDialog.getText(self, "Edit memory", f"{fact.attribute}:", QLineEdit.Normal, fact.value)
        value = value.strip()
        if ok and value and value != fact.value:
            self.backend.run_io(lambda: self.memory.edit(fact.id, value), lambda _: self.refresh(), self._failed)

    def _pin(self):
        fact = self._require("Pin memory")
        if fact is not None:
            self.backend.run_io(lambda: self.memory.pin(fact.id, not fact.pinned), lambda _: self.refresh(),
                                self._failed)

    def _why(self):
        fact = self._require("How do you know?")
        if fact is not None:
            self.backend.run_io(lambda: self.memory.provenance(fact),
                                lambda text: QMessageBox.information(self, f"{fact.attribute}: {fact.value}", text),
                                self._failed)

    def _delete(self):
        fact = self._require("Delete memory")
        if fact is None:
            return
        answer = QMessageBox.question(
            self, "Delete memory",
            f"Delete this memory permanently?\n\n{fact.attribute}: {fact.value}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.backend.run_io(lambda: self.memory.forget(fact.id), lambda _: self.refresh(), self._failed)

    def _block(self):
        fact = self._require("Don't learn again")
        if fact is None:
            return
        answer = QMessageBox.question(
            self, "Delete and don't learn again",
            f"Delete this memory and never learn it again?\n\n{fact.attribute}: {fact.value}\n\n"
            "You can allow it again under Blocked….",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            def block():
                self.memory.block(fact)
                return self.memory.forget(fact.id)
            self.backend.run_io(block, lambda _: self.refresh(), self._failed)

    # --- Switches and lists -------------------------------------------------------

    def _set_learning(self, on: bool):
        if self.memory is not None:
            self.backend.run_io(lambda: self.memory.set_learning_paused(not on), lambda _: self._fill(), self._failed)

    def _set_activity(self, on: bool):
        if self.engine is not None:
            self.backend.run_io(lambda: self.engine.episodes.set_enabled(on), None, self._failed)

    def _show_activity(self):
        if self.engine is None:
            return
        log = self.engine.episodes

        def show(episodes):
            rows = [(e.line(), e.id) for e in episodes]
            ListDialog(self, "Activity history",
                       f"One line per task AURORA did for you, kept for {settings.EPISODE_RETENTION_DAYS} days. "
                       "It never contains the text of your messages.",
                       rows, extra="Clear all", on_extra=lambda: self.backend.run_io(log.clear, None, self._failed)
                       ).exec()

        self.backend.run_io(lambda: log.recent(limit=200), show, self._failed)

    def _show_routines(self):
        if self.engine is None:
            return
        store = self.engine.routines

        def show(routines):
            rows = [(f"“{r.name}”: {r.steps}  (run {r.times_run}×)", r.name) for r in routines]
            ListDialog(self, "Routines",
                       "Routines you taught AURORA. Say a routine's name to run it. To add one, tell AURORA "
                       "\"when I say <name>, <what to do>\".",
                       rows, action="Delete", on_action=lambda name: self.backend.run_io(
                           lambda: store.delete(name), None, self._failed)).exec()

        self.backend.run_io(store.all, show, self._failed)

    def _show_blocked(self):
        if self.memory is None:
            return
        memory = self.memory

        def show(blocked):
            rows = [(f"{'' if b['entity'] == 'User' else b['entity'] + ' — '}{b['attribute']}: {b['value']}", b["id"])
                    for b in blocked]
            ListDialog(self, "Blocked memories",
                       "Things you told AURORA not to remember. It won't learn them again unless you allow them.",
                       rows, action="Allow again", on_action=lambda block_id: self.backend.run_io(
                           lambda: memory.unblock(block_id), None, self._failed)).exec()

        self.backend.run_io(memory.blocked, show, self._failed)

    def _failed(self, error):
        QMessageBox.warning(self, "Memory", f"That didn't work: {error}")
