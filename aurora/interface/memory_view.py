"""Memory Centre: inspect, search, edit and delete AURORA's long-term memories."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem,
)

from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import label
from aurora.knowledge.fact import Fact

COLUMNS = ["ID", "Category", "Attribute", "Value", "Priority", "Confidence", "Created", "Last used", "Used"]
ALL = "All categories"


class MemoryView(BaseView):
    title = "Memory Centre"
    subtitle = ("Everything AURORA remembers about you, from its SQLite memory. "
                "Edit or delete anything; nothing is locked.")
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
            ("Add note", self._add, None), ("Edit value", self._edit, None),
            ("Delete", self._delete, "Danger"), ("Refresh", self.refresh, None),
        ]:
            button = QPushButton(text)
            if name:
                button.setObjectName(name)
            button.clicked.connect(handler)
            tools.addWidget(button)
        self.body.addLayout(tools)

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
    def memory(self):
        return self.backend.engine.memory if self.backend.engine is not None else None

    def on_engine_ready(self):
        self.refresh()

    def on_shown(self):
        self.refresh()

    def refresh(self):
        memory = self.memory
        if memory is None:
            self.summary.setText("AURORA is starting…")
            return
        self.backend.run_io(memory.memories, self._loaded,
                            lambda error: self.summary.setText(f"Couldn't read memories: {error}"))

    def _loaded(self, facts: list[Fact]):
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
            and (not query or query in f"{fact.category} {fact.attribute} {fact.value}".lower())
        ]

    def _fill(self):
        facts = self._visible()
        self.table.setRowCount(len(facts))
        for row, fact in enumerate(facts):
            values = [
                str(fact.id), fact.category, fact.attribute, fact.value, str(fact.priority),
                f"{fact.confidence:.2f}",
                fact.created.strftime("%d %b %Y %H:%M") if fact.created else "—",
                fact.last_used.strftime("%d %b %Y %H:%M") if fact.last_used else "never",
                str(fact.times_used),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (0, 4, 5, 8):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, column, item)
        self.summary.setText(f"Showing {len(facts)} of {len(self.facts)} memories.")

    def _selected(self) -> Fact | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        memory_id = int(self.table.item(rows[0].row(), 0).text())
        return next((f for f in self.facts if f.id == memory_id), None)

    # --- Changes (all through MemoryManager) ------------------------------------

    def _add(self):
        if self.memory is None:
            return
        text, ok = QInputDialog.getText(self, "Add a note", "What should AURORA remember?")
        text = text.strip()
        if ok and text:
            fact = Fact("Note", "User", "Note", text, 3)
            self.backend.run_io(lambda: self.memory.remember(fact), lambda _: self.refresh(), self._failed)

    def _edit(self):
        fact = self._selected()
        if fact is None:
            QMessageBox.information(self, "Edit memory", "Select a memory first.")
            return
        value, ok = QInputDialog.getText(self, "Edit memory", f"{fact.attribute}:", QLineEdit.Normal, fact.value)
        value = value.strip()
        if ok and value and value != fact.value:
            self.backend.run_io(lambda: self.memory.edit(fact.id, value), lambda _: self.refresh(), self._failed)

    def _delete(self):
        fact = self._selected()
        if fact is None:
            QMessageBox.information(self, "Delete memory", "Select a memory first.")
            return
        answer = QMessageBox.question(
            self, "Delete memory",
            f"Delete this memory permanently?\n\n{fact.attribute}: {fact.value}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.backend.run_io(lambda: self.memory.forget(fact.id), lambda _: self.refresh(), self._failed)

    def _failed(self, error):
        QMessageBox.warning(self, "Memory", f"That didn't work: {error}")
