"""Home dashboard: identity, time, real system state and recent activity."""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from aurora.config import settings
from aurora.interface import system_info
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import (
    Emblem, Panel, StatCard, StatusDot, StatusRow, clear_layout, label,
)


def greeting(now: datetime) -> str:
    if now.hour < 5:
        return "Working late"
    if now.hour < 12:
        return "Good morning"
    if now.hour < 17:
        return "Good afternoon"
    return "Good evening"


class DashboardView(BaseView):
    title = "Dashboard"
    nav_icon = "◈"

    navigate = Signal(str)       # Asks the main window to open a page.

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.body.itemAt(0).widget().hide()   # The hero row replaces the page header.

        # --- Hero: identity, greeting, clock -----------------------------------
        hero = Panel()
        row = QHBoxLayout()
        self.emblem = Emblem(64)
        row.addWidget(self.emblem)
        identity = QVBoxLayout()
        self.welcome = label("", "PageTitle")
        identity.addWidget(self.welcome)
        identity.addWidget(label(f"AURORA Command Centre · version {settings.VERSION}", "Dim"))
        status_line = QHBoxLayout()
        self.status_dot = StatusDot("warning", pulse=True)
        self.status_text = label("Starting…", "Dim")
        status_line.addWidget(self.status_dot)
        status_line.addWidget(self.status_text)
        status_line.addStretch(1)
        identity.addLayout(status_line)
        row.addLayout(identity, 1)
        clock = QVBoxLayout()
        self.clock = label("", "Clock")
        self.clock.setAlignment(Qt.AlignRight)
        self.date = label("", "Dim")
        self.date.setAlignment(Qt.AlignRight)
        clock.addWidget(self.clock)
        clock.addWidget(self.date)
        row.addLayout(clock)
        hero.add_layout(row)
        self.body.addWidget(hero)

        # --- Stat cards ---------------------------------------------------------
        cards = QGridLayout()
        cards.setSpacing(14)
        self.ai_card = StatCard("AI status")
        self.model_card = StatCard("Language model")
        self.memory_card = StatCard("Memory")
        self.cpu_card = StatCard("CPU", meter=True)
        self.ram_card = StatCard("RAM", meter=True)
        for column, card in enumerate([self.ai_card, self.model_card, self.memory_card, self.cpu_card, self.ram_card]):
            cards.addWidget(card, 0, column)
        self.body.addLayout(cards)

        # --- Services, activity, quick actions ------------------------------------
        lower = QHBoxLayout()
        lower.setSpacing(14)

        self.services = Panel("Services & plugins")
        self.service_rows = QVBoxLayout()
        self.services.add_layout(self.service_rows)
        self.services.layout_.addStretch(1)
        lower.addWidget(self.services, 2)

        self.activity = Panel("Recent activity")
        self.activity_rows = QVBoxLayout()
        self.activity_rows.setSpacing(4)
        self.activity.add_layout(self.activity_rows)
        self.activity.layout_.addStretch(1)
        lower.addWidget(self.activity, 3)

        quick = Panel("Quick access")
        for text, page in [
            ("💬  New conversation", "Chat"), ("🧠  Memory Centre", "Memory"),
            ("📚  Knowledge Centre", "Knowledge"), ("🌡  Sensor readings", "Sensors"),
            ("📈  System monitor", "System"),
        ]:
            button = QPushButton(text)
            button.setObjectName("Quick")
            button.clicked.connect(lambda _=False, p=page: self._quick(p))
            quick.add(button)
        quick.layout_.addStretch(1)
        lower.addWidget(quick, 2)

        self.body.addLayout(lower)
        self.body.addStretch(1)

        # --- Updates ---------------------------------------------------------------
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start(1000)
        self._tick()

        self._memory_timer = QTimer(self)
        self._memory_timer.timeout.connect(self._refresh_memory)

        backend.system_sampled.connect(self._show_system)
        backend.ollama_checked.connect(lambda _: self._refresh_status())
        backend.activity_added.connect(lambda _: self._refresh_activity())
        backend.engine_failed.connect(self._failed)
        backend.preferences_changed.connect(lambda _: self._refresh_status())
        self._show_system(backend.last_sample)
        self._refresh_activity()

    # --- Slots -------------------------------------------------------------------

    def _quick(self, page: str):
        if page == "Chat":
            self.backend.clear_conversation()
        self.navigate.emit(page)

    def _tick(self):
        now = datetime.now()
        self.welcome.setText(f"{greeting(now)}, {settings.USER_NAME}.")
        self.clock.setText(now.strftime("%H:%M:%S"))
        self.date.setText(now.strftime("%A, %d %B %Y"))

    def on_engine_ready(self):
        self._refresh_status()
        self._refresh_memory()
        self._memory_timer.start(10_000)

    def on_shown(self):
        self._refresh_status()
        self._refresh_memory()

    def _failed(self, message: str):
        self.status_dot.set_state("error", pulse=False)
        self.status_text.setText(message)
        self.ai_card.set("Offline", message)

    def _refresh_status(self):
        status = self.backend.status()
        running = status.get("running", False)
        services = status.get("services", {})
        brain_ok = services.get("Brain") == "Running"

        if not running:
            self.ai_card.set("Starting…", "Loading AURORA's kernel")
        elif brain_ok:
            self.ai_card.set("Online", "Kernel running · Brain ready")
        else:
            self.ai_card.set("Degraded", f"Brain service: {services.get('Brain', 'unknown')}")

        ollama = self.backend.ollama
        model = status.get("model", settings.DEFAULT_MODEL)
        if ollama["ok"] is None:
            self.model_card.set(model, "Checking Ollama…")
        elif ollama["ok"]:
            self.model_card.set(model, "Ollama connected")
        else:
            self.model_card.set(model, f"Ollama: {ollama['detail']}")

        if running and brain_ok and ollama["ok"]:
            self.status_dot.set_state("ok", pulse=True)
            self.status_text.setText("All core systems operational")
        elif running:
            self.status_dot.set_state("warning", pulse=True)
            self.status_text.setText("Running with problems: see System monitor")

        # Services, plugins and tools
        clear_layout(self.service_rows)
        for name, state in services.items():
            self.service_rows.addWidget(StatusRow(f"Service · {name}", state, "ok" if state == "Running" else "error"))
        plugins = status.get("plugins", {})
        if plugins:
            for name, state in plugins.items():
                self.service_rows.addWidget(StatusRow(f"Plugin · {name}", state, "ok" if state == "Running" else "off"))
        else:
            self.service_rows.addWidget(label("No plugins registered.", "Faint"))
        tools = status.get("tools", {})
        if tools:
            ready = sum(1 for state in tools.values() if state == "Ready")
            self.service_rows.addWidget(StatusRow("Tools", f"{ready} of {len(tools)} ready",
                                                  "ok" if ready == len(tools) else "warning"))

    def _refresh_memory(self):
        engine = self.backend.engine
        if engine is None:
            return

        def count():
            return len(engine.memory.memories())

        self.backend.run_io(
            count,
            lambda n: self.memory_card.set(str(n), f"memories stored · learning "
                                           f"{'on' if self.backend.status().get('auto_memory') else 'off'}"),
            lambda error: self.memory_card.set("Error", str(error)),
        )

    def _show_system(self, sample: dict):
        if not sample:
            return
        cpu = sample.get("cpu")
        cores = sample.get("cores")
        self.cpu_card.set(f"{cpu:.0f}%" if cpu is not None else "—",
                          f"{cores} logical cores" if cores else "unavailable", cpu)
        memory = sample.get("memory")
        if memory is not None:
            self.ram_card.set(f"{memory.percent:.0f}%",
                              f"{system_info.human_bytes(memory.used_bytes)} of "
                              f"{system_info.human_bytes(memory.total_bytes)} · AURORA "
                              f"{system_info.human_bytes(sample.get('process'))}", memory.percent)
        else:
            self.ram_card.set("—", "unavailable on this system")

    def _refresh_activity(self):
        clear_layout(self.activity_rows)
        recent = list(self.backend.activities)[:8]
        if not recent:
            self.activity_rows.addWidget(label("Nothing yet. Activity appears here as AURORA works.", "Faint"))
        for activity in recent:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(StatusDot(activity.kind))
            line.addWidget(label(activity.time.strftime("%H:%M:%S"), "Faint"))
            text = QLabel(activity.text)
            text.setWordWrap(True)
            line.addWidget(text, 1)
            self.activity_rows.addWidget(row)

    def set_accent(self, colour: str):
        self.emblem.set_accent(colour)
