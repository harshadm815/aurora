"""System monitor: real resource use, connection state, services and logs."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton

from aurora.config import settings
from aurora.providers.core_provider import is_core_model
from aurora.interface import system_info
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Panel, StatCard, StatusRow, discard, label

LEVEL_COLOURS = {"WARNING": "#fbbf24", "ERROR": "#f87171", "CRITICAL": "#f87171"}


class SystemMonitorView(BaseView):
    title = "System monitor"
    subtitle = "Live readings from this computer and AURORA. Nothing here is estimated."
    nav_icon = "📈"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cpu = StatCard("CPU", meter=True)
        self.ram = StatCard("System RAM", meter=True)
        self.process = StatCard("AURORA memory")
        self.uptime = StatCard("Uptime")
        for column, card in enumerate([self.cpu, self.ram, self.process, self.uptime]):
            grid.addWidget(card, 0, column)
        self.body.addLayout(grid)

        row = QHBoxLayout()
        row.setSpacing(14)
        self.connections = Panel("Connections")
        self.ollama_row = self.connections.add(StatusRow("Ollama", "Checking…", "warning"))
        self.model_row = self.connections.add(StatusRow("Active model", settings.DEFAULT_MODEL, "info"))
        self.models_row = self.connections.add(StatusRow("Installed models", "", "info"))
        check = QPushButton("Check now")
        check.clicked.connect(self.backend.check_ollama)
        self.connections.add(check)
        self.connections.layout_.addStretch(1)
        row.addWidget(self.connections, 1)

        self.components = Panel("Services, plugins and tools")
        self.component_rows = []
        self.components.layout_.addStretch(1)
        row.addWidget(self.components, 1)
        self.body.addLayout(row)

        logs = Panel("Logs")
        controls = QHBoxLayout()
        self.level = QComboBox()
        self.level.addItems(["All", "Warnings and errors", "Errors only"])
        self.level.currentIndexChanged.connect(self._fill_logs)
        controls.addWidget(label("Show:", "Dim"))
        controls.addWidget(self.level)
        controls.addStretch(1)
        controls.addWidget(label(f"Full log: {settings.LOG_FILE} (never contains your messages)", "Faint"))
        logs.add_layout(controls)
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(240)
        logs.add(self.log_list)
        self.body.addWidget(logs)

        backend.system_sampled.connect(self._show_system)
        backend.ollama_checked.connect(self._show_ollama)
        backend.log_added.connect(self._add_log)
        backend.preferences_changed.connect(lambda _: self.on_shown())

    def _show_system(self, sample: dict):
        if not self.isVisible() or not sample:
            return
        cpu = sample.get("cpu")
        self.cpu.set(f"{cpu:.0f}%" if cpu is not None else "—",
                     f"{sample.get('cores')} logical cores" if cpu is not None else "unavailable", cpu)
        memory = sample.get("memory")
        if memory:
            self.ram.set(f"{memory.percent:.0f}%", f"{system_info.human_bytes(memory.used_bytes)} used of "
                         f"{system_info.human_bytes(memory.total_bytes)}", memory.percent)
        else:
            self.ram.set("—", "unavailable")
        self.process.set(system_info.human_bytes(sample.get("process")),
                         "Command Centre and AURORA (Ollama runs separately)")
        self.uptime.set(system_info.human_duration(sample.get("uptime", 0)), "since the Command Centre started")

    def _show_ollama(self, state: dict):
        if state["ok"] is None:
            self.ollama_row.set("Checking…", "warning")
        elif state["ok"] and is_core_model(settings.DEFAULT_MODEL):
            self.ollama_row.set("Not needed: AURORA's own model is in use", "ok")
        elif state["ok"]:
            self.ollama_row.set("Connected", "ok")
        else:
            self.ollama_row.set(state["detail"][:80], "error")
        models = state.get("models") or []
        self.models_row.set(", ".join(models) if models else "unknown", "info")
        self.model_row.set(self.backend.status().get("model", settings.DEFAULT_MODEL), "info")

    def on_engine_ready(self):
        self.on_shown()

    def on_shown(self):
        self._show_system(self.backend.last_sample)
        self._show_ollama(self.backend.ollama)
        for row in self.component_rows:
            discard(row)
        self.component_rows = []
        status = self.backend.status()
        rows = []
        for name, state in status.get("services", {}).items():
            rows.append(StatusRow(f"Service · {name}", state, "ok" if state == "Running" else "error"))
        for name, state in status.get("plugins", {}).items():
            rows.append(StatusRow(f"Plugin · {name}", state, "ok" if state == "Running" else "off"))
        for name, state in status.get("tools", {}).items():
            error = self.backend.tool_errors.get(name)
            rows.append(StatusRow(f"Tool · {name}", "Error" if error else state,
                                  "error" if error else ("ok" if state == "Ready" else "warning")))
        if not rows:
            rows.append(StatusRow("AURORA", "Starting…", "warning"))
        for position, row in enumerate(rows):
            self.components.layout_.insertWidget(position + 1, row)
        self.component_rows = rows
        self._fill_logs()

    def _wanted(self, line) -> bool:
        choice = self.level.currentIndex()
        if choice == 1:
            return line.level in ("WARNING", "ERROR", "CRITICAL")
        if choice == 2:
            return line.level in ("ERROR", "CRITICAL")
        return True

    def _item(self, line) -> QListWidgetItem:
        item = QListWidgetItem(f"{line.time:%H:%M:%S}  {line.level:<8} {line.name}: {line.message}")
        if line.level in LEVEL_COLOURS:
            item.setForeground(QColor(LEVEL_COLOURS[line.level]))
        return item

    def _fill_logs(self):
        self.log_list.clear()
        for line in self.backend.logs:
            if self._wanted(line):
                self.log_list.addItem(self._item(line))
        self.log_list.scrollToBottom()

    def _add_log(self, line):
        if self._wanted(line):
            self.log_list.addItem(self._item(line))
            if self.log_list.count() > 500:
                self.log_list.takeItem(0)
            self.log_list.scrollToBottom()
