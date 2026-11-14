"""Tools and capabilities: what AURORA can do right now, and why not if it can't."""

from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
)

from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Panel, label

DESCRIPTIONS = {
    "AutoMemory": "Learns facts from conversation and gives relevant memories to the model",
    "Calculator": "Safe arithmetic (no eval): numbers and operators only",
    "Remember": "Saves things you ask AURORA to remember",
    "Recall": "Searches your memories by keyword",
    "Forget": "Deletes memories, only after you confirm",
    "Weather": "Real forecasts from Open-Meteo (needs network)",
    "Wikipedia": "Encyclopedia summaries with links (needs network and RESEARCH_CONTACT)",
    "arXiv": "Research papers (needs network)",
    "News": "News on any topic, only when you ask: sources, dates, links and how well each story is confirmed (needs network)",
    "Documents": "Lists, searches and reads files in your Documents folder only",
    "Camera": "One picture on request: objects and text, never saved",
    "Sensors": "Room temperature, humidity, pressure, light, motion, CO₂, VOCs and dust, with what they mean and how they changed",
    "Health": "Heart rate and SpO2 (informational, separate permission)",
    "Devices": "Switches configured devices, only after you confirm",
    "Reminders": "Reminders and timers, kept if AURORA closes (missed ones are reported)",
    "Apps": "Opens programs from the APPS list; closes only what it opened (asks first)",
    "Files": "Tidies allowed folders by type or month: previewed, confirmed, undoable",
    "Schedule": "Tasks you approved once that run at set times (reminders, routines, sensors, devices)",
    "Research": "Runs the research agent team",
    "Numbers": "Averages, medians, percentages and more, calculated exactly, with the working shown",
    "MemoryControl": "\"Don't remember that\", pause learning, activity history on/off",
    "Briefing": "Today's reminders, scheduled tasks and recent research: \"give me my briefing\"",
    "Episodes": "Your activity history: \"what did we do today?\" (summaries only, 30 days)",
    "Routines": "Routines you teach: \"when I say morning briefing, …\"",
    "Knowledge": "Answers from your documents, quoted with citations; summaries and comparisons",
}


class ToolsView(BaseView):
    title = "Tools & capabilities"
    subtitle = "Every capability, with its real state. Blocked tools show which permission they need."
    nav_icon = "🛠"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)

        panel = Panel("Capabilities")
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Tool", "Status", "What it does"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setMinimumHeight(460)
        panel.add(self.table)
        panel.add(label("Permissions are set in PERMISSION_GRANTS in aurora/config/settings.py "
                        "(or local_settings.py). The interface can't grant them.", "Faint", wrap=True))
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.on_shown)
        panel.add(refresh)
        self.body.addWidget(panel)

        trial = Panel("Try the calculator")
        row = QHBoxLayout()
        self.expression = QLineEdit()
        self.expression.setPlaceholderText("e.g. 12 * (7 + 3)")
        self.expression.returnPressed.connect(self._calculate)
        go = QPushButton("Calculate")
        go.clicked.connect(self._calculate)
        row.addWidget(self.expression, 1)
        row.addWidget(go)
        trial.add_layout(row)
        self.answer = trial.add(label("", "Accent"))
        self.body.addWidget(trial)
        self.body.addStretch(1)

    def on_engine_ready(self):
        self.on_shown()

    def on_shown(self):
        engine = self.backend.engine
        if engine is None:
            return
        statuses = engine.tool_status()
        errors = self.backend.tool_errors
        self.table.setRowCount(len(statuses))
        for row, (name, state) in enumerate(statuses.items()):
            if name in errors:
                text, colour = f"Error: {errors[name]}", "#f87171"
            elif state == "Ready":
                text, colour = "Available", "#34d399"
            else:
                text, colour = state.replace("Blocked", "Disabled"), "#fbbf24"
            name_item = QTableWidgetItem(name)
            status_item = QTableWidgetItem(f"● {text}")
            status_item.setForeground(_brush(colour))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, status_item)
            self.table.setItem(row, 2, QTableWidgetItem(DESCRIPTIONS.get(name, "")))

    def _calculate(self):
        expression = self.expression.text().strip()
        engine = self.backend.engine
        if not expression or engine is None:
            return
        self.backend.run_io(
            lambda: engine.tool_manager.execute("Calculator", expression=expression),
            lambda result: self.answer.setText(result.message),
            lambda error: self.answer.setText(f"Error: {error}"),
        )


def _brush(colour: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(colour))
