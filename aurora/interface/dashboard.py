"""
Home dashboard: AURORA's core and what it is doing, the state of the
system, what's coming up, and a box to ask AURORA something.

    greeting row     greeting, date, Pause / Start
    centre           the AI core (what AURORA is doing now), its state in
                     words, the voice waveform, performance mode when it's
                     on, and round gauges: CPU, RAM, AURORA's own memory,
                     tools ready
    right column     AI status (with the services), model, memory, room;
                     connections (internet access, browser tabs, research
                     projects); a panel with two tabs: Coming up (reminders
                     and scheduled tasks) and Recent activity
    bottom bar       ask AURORA (opens Chat with the question), microphone,
                     picture

(The window's top bar, on every page, shows the AI state, model,
internet access, CPU/RAM and the time.) Everything shown is real state.
"""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout,
                               QWidget)

from aurora.config import settings
from aurora.interface import system_info
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.core_orb import LABELS, CoreOrb
from aurora.interface.components.gauges import RingGauge
from aurora.interface.components.waveform import Waveform
from aurora.interface.components.widgets import Panel, StatCard, StatusDot, clear_layout, label
from aurora.network import gate as network_gate
from aurora.providers.core_provider import is_core_model


def greeting(now: datetime) -> str:
    if now.hour < 5:
        return "Working late"
    if now.hour < 12:
        return "Good morning"
    if now.hour < 17:
        return "Good afternoon"
    return "Good evening"


def state_detail(state: str, model: str) -> str:
    """One line under the core saying what the state means right now."""
    return {
        "idle": f"Ready for your next request · {model}",
        "thinking": "Working on your request",
        "responding": "Writing the reply",
        "speaking": "Speaking the reply (Stop in Chat interrupts it)",
        "listening": "Listening: say your request",
        "processing": "Starting AURORA's kernel and services",
        "error": "The last request failed: see Chat or the System monitor",
        "offline": "Paused: services stopped and the model unloaded from memory",
    }.get(state, "")


def upcoming(reminders, tasks, now: datetime, limit: int = 5) -> list[tuple[datetime, str, str]]:
    """(when, what, kind) for the next pending reminders and active scheduled tasks, soonest first."""
    items = [(r.due, r.text, "Reminder") for r in reminders]
    for task in tasks:
        if not getattr(task, "active", True):
            continue
        when = task.next_run(now)
        if when is not None:
            items.append((when, task.name, "Scheduled"))
    return sorted(items, key=lambda item: item[0])[:limit]


def internet_line(mode: str, approved: list[str], services: dict) -> str:
    """Internet access and which services may go online, in words."""
    names = [services.get(c, {}).get("name", c) for c in approved]
    if mode == "off":
        return "Off: nothing goes to the internet"
    if mode == "on":
        return "On: services may go online without asking"
    return "Asks first · " + ("allowed this session: " + ", ".join(names) if names else "nothing allowed yet")


def browser_line(tabs: list[tuple[str, str]] | None) -> str:
    """The Browser's open tabs, in words (tabs is None until the Browser is first opened)."""
    if not tabs:
        return "Not open" if tabs is None else "No tabs open"
    hosts = list(dict.fromkeys(host for _, host in tabs if host))
    text = f"{len(tabs)} tab{'s' if len(tabs) != 1 else ''} open"
    if hosts:
        text += " · " + ", ".join(hosts[:3]) + (f" +{len(hosts) - 3}" if len(hosts) > 3 else "")
    return text


def research_line(projects) -> str:
    """Research projects and the latest session, in words."""
    if not projects:
        return "No projects yet · try “continue my research on …” in Chat"
    sessions = sum(len(p.sessions) for p in projects)
    latest = max(((s.time, p.name) for p in projects for s in p.sessions), default=None)
    text = (f"{len(projects)} project{'s' if len(projects) != 1 else ''} · "
            f"{sessions} session{'s' if sessions != 1 else ''}")
    if latest is not None:
        text += f" · last: {latest[1]}, {latest[0]:%d %b}"
    return text


def when_text(moment: datetime, now: datetime) -> str:
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if (moment.date() - now.date()).days == 1:
        return moment.strftime("tomorrow %H:%M")
    return moment.strftime("%a %d %b %H:%M")


class DashboardView(BaseView):
    title = "Dashboard"
    nav_icon = "◈"
    scrollable = False             # the ask bar stays at the bottom

    navigate = Signal(str)          # asks the main window to open a page
    ask = Signal(str)               # a question typed on the dashboard (answered in Chat)
    listen_requested = Signal()     # the microphone button
    picture_requested = Signal()    # the picture button

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.body.itemAt(0).widget().hide()          # the greeting row replaces the page header
        self.body.setSpacing(14)

        # --- Greeting ----------------------------------------------------------------
        top = QHBoxLayout()
        words = QVBoxLayout()
        words.setSpacing(0)
        self.welcome = label("", "PageTitle")
        self.date = label("", "Dim")
        words.addWidget(self.welcome)
        words.addWidget(self.date)
        top.addLayout(words, 1)
        self.pause_button = QPushButton("⏸  Pause AURORA")
        self.pause_button.setToolTip("Stop AURORA's services and free the model's memory")
        self.pause_button.clicked.connect(self._toggle_pause)
        top.addWidget(self.pause_button, 0, Qt.AlignVCenter)
        self.body.addLayout(top)

        # --- Centre and right column -------------------------------------------------------
        main = QHBoxLayout()
        main.setSpacing(14)

        centre = Panel("AURORA core")
        centre.setObjectName("CorePanel")
        self.orb = CoreOrb()
        self.orb.setMinimumSize(200, 200)
        centre.add(self.orb, 1)
        self.state_label = label("", "OrbState")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_detail = label("", "Dim", wrap=True)
        self.state_detail.setAlignment(Qt.AlignCenter)
        centre.add(self.state_label)
        centre.add(self.state_detail)
        self.waveform = Waveform()
        centre.add(self.waveform)
        self.performance_label = label("", "Faint", wrap=True)
        self.performance_label.setAlignment(Qt.AlignCenter)
        self.performance_label.setToolTip("Settings → Interface → Performance mode")
        self.performance_label.hide()
        centre.add(self.performance_label)
        gauges = QHBoxLayout()
        gauges.setSpacing(4)
        self.cpu_gauge = RingGauge("CPU")
        self.ram_gauge = RingGauge("RAM")
        self.process_gauge = RingGauge("AURORA")
        self.tools_gauge = RingGauge("Tools", more_is_better=True)
        for gauge in (self.cpu_gauge, self.ram_gauge, self.process_gauge, self.tools_gauge):
            gauges.addWidget(gauge)
        centre.add_layout(gauges)
        main.addWidget(centre, 5)

        right = QVBoxLayout()
        right.setSpacing(10)
        cards = QGridLayout()
        cards.setSpacing(10)
        self.ai_card = StatCard("AI status")
        self.model_card = StatCard("Language model")
        self.memory_card = StatCard("Memory")
        self.room_card = StatCard("Room")
        self.room_card.set("—", "Background watching is off (Sensor Centre).")
        for index, card in enumerate((self.ai_card, self.model_card, self.memory_card, self.room_card)):
            card.setMinimumHeight(100)           # value and detail stay readable on a short screen
            cards.addWidget(card, index // 2, index % 2)
        right.addLayout(cards)

        self.connections = Panel("Connections")
        rows = QGridLayout()
        rows.setHorizontalSpacing(8)
        rows.setVerticalSpacing(2)
        self.internet_row = self._connection(rows, 0, "🌐", "Internet", "Settings")
        self.browser_row = self._connection(rows, 1, "🧭", "Browser", "Browser")
        self.research_row = self._connection(rows, 2, "🔬", "Research", "Knowledge")
        rows.setColumnStretch(1, 1)
        self.connections.add_layout(rows)
        right.addWidget(self.connections)
        self.browser_tabs = lambda: None        # set by the main window: BrowserView.open_tabs

        # Coming up and Recent activity share one panel (two tabs), so everything fits a 768-pixel screen.
        self.timeline = Panel()
        self.timeline_tabs = QTabWidget()
        self.timeline_tabs.setDocumentMode(True)
        self.upcoming_rows = self._tab_rows("Coming up")
        self.activity_rows = self._tab_rows("Recent activity")
        self.timeline.add(self.timeline_tabs, 1)
        right.addWidget(self.timeline, 1)
        main.addLayout(right, 4)
        self.body.addLayout(main, 1)

        # --- Ask bar -------------------------------------------------------------------------
        bar = Panel()
        bar.setObjectName("AskBar")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.picture_button = QPushButton("🖼")
        self.picture_button.setToolTip("Ask about a picture (opens Chat)")
        self.picture_button.clicked.connect(self.picture_requested.emit)
        self.ask_box = QLineEdit()
        self.ask_box.setObjectName("AskBox")
        self.ask_box.setPlaceholderText("Ask AURORA anything…  (the answer appears in Chat)")
        self.ask_box.returnPressed.connect(self._ask)
        self.mic_button = QPushButton("🎙")
        self.mic_button.setToolTip("Ask by voice (opens Chat; the microphone opens for one request)")
        self.mic_button.clicked.connect(self.listen_requested.emit)
        send = QPushButton("Ask")
        send.setObjectName("Primary")
        send.clicked.connect(self._ask)
        for widget in (self.picture_button, self.ask_box, self.mic_button, send):
            row.addWidget(widget, 1 if widget is self.ask_box else 0)
        bar.add_layout(row)
        self.body.addWidget(bar)

        # --- Updates -----------------------------------------------------------------------
        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick)
        self._clock_timer.start(1000)
        self._tick()
        self._slow_timer = QTimer(self)                 # memory count, coming up
        self._slow_timer.timeout.connect(self._refresh_slow)

        backend.system_sampled.connect(self._show_system)
        backend.ollama_checked.connect(lambda _: self._refresh_status())
        backend.activity_added.connect(lambda _: self._refresh_activity())
        backend.engine_failed.connect(self._failed)
        backend.preferences_changed.connect(self._preferences)
        backend.state_changed.connect(lambda _: self._refresh_status())
        backend.ai_state_changed.connect(self._show_ai_state)
        backend.voice.level.connect(self._voice_level)
        backend.performance_changed.connect(self._performance)
        backend.sensor_readings.connect(self.show_room)
        backend.sensor_monitoring.connect(
            lambda on: None if on else self.room_card.set("—", "Background sensor watching is off."))
        self._preferences(backend.preferences)
        self._show_ai_state(getattr(backend, "ai_state", "processing"))
        self._show_system(backend.last_sample)
        self._refresh_activity()
        self._show_upcoming([])
        self._performance(getattr(backend, "performance_reduced", False), getattr(backend, "performance_text", ""))
        self._refresh_connections()

    def _tab_rows(self, title: str) -> QVBoxLayout:
        page = QWidget()
        rows = QVBoxLayout()
        rows.setSpacing(4)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(2, 6, 2, 0)
        outer.addLayout(rows)
        outer.addStretch(1)
        self.timeline_tabs.addTab(page, title)
        return rows

    def _connection(self, grid: QGridLayout, row: int, icon: str, name: str, page: str) -> QLabel:
        go = QPushButton(f"{icon}  {name}")
        go.setObjectName("Crumb")
        go.setCursor(Qt.PointingHandCursor)
        go.setToolTip(f"Open {page}")
        go.clicked.connect(lambda: self.navigate.emit(page))
        grid.addWidget(go, row, 0)
        text = label("", "Dim", wrap=True)
        grid.addWidget(text, row, 1)
        return text

    # --- Slots -------------------------------------------------------------------

    def _voice_level(self, rms: float, _speaking: bool):
        self.orb.set_level(rms / 3000)
        self.waveform.push(rms / 3000)

    def _performance(self, reduced: bool, text: str):
        self.orb.set_reduced(reduced)
        self.waveform.set_reduced(reduced)
        self.performance_label.setText(text)
        self.performance_label.setVisible(bool(text))

    def _refresh_connections(self, projects=None):
        self.internet_row.setText(internet_line(network_gate.mode, network_gate.approved(),
                                                settings.NETWORK_SERVICES))
        self.browser_row.setText(browser_line(self.browser_tabs()))
        if projects is not None:
            self.research_row.setText(research_line(projects))
        elif not self.research_row.text():
            self.research_row.setText("Loading…" if self.backend.engine is not None else "Starts with AURORA")

    def _ask(self):
        text = self.ask_box.text().strip()
        if text:
            self.ask_box.clear()
            self.ask.emit(text)

    def _toggle_pause(self):
        if self.backend.state in ("paused", "failed"):
            self.backend.resume()
        else:
            self.backend.pause()

    def _preferences(self, values: dict):
        self.orb.set_animated(bool(values.get("animations", True)))
        self.waveform.set_animated(bool(values.get("animations", True)))

    def _tick(self):
        now = datetime.now()
        self.welcome.setText(f"{greeting(now)}, {settings.USER_NAME}.")
        self.date.setText(now.strftime("%A, %d %B %Y") + f"  ·  AURORA {settings.VERSION}")

    def on_engine_ready(self):
        self._refresh_status()
        self._refresh_slow()
        self._slow_timer.start(10_000)

    def on_shown(self):
        self._refresh_status()
        self._refresh_slow()
        self._refresh_connections()

    def _failed(self, message: str):
        self.ai_card.set("Offline", message)
        self.state_detail.setText(message)

    def _show_ai_state(self, state: str):
        self.orb.set_state(state)
        self.waveform.set_state(state)
        self.state_label.setText(LABELS.get(state, state))
        model = self.backend.status().get("model", settings.DEFAULT_MODEL) if self.backend.engine else \
            settings.DEFAULT_MODEL
        self.state_detail.setText(state_detail(state, model))

    def show_room(self, data: dict):
        """The room at a glance, from the Sensor Service's latest readings."""
        from aurora.interface.sensors_view import readings_from_event
        from aurora.sensors.interpret import POOR, interpret

        readings, stale = readings_from_event(data)
        fresh = {q: r for q, r in readings.items() if q not in stale}
        temperature = fresh.get("temperature")
        value = temperature.formatted() if temperature else "—"
        parts = []
        poor = [r.spec.label for r in fresh.values() if (m := interpret(r)) is not None and m.level == POOR]
        air = fresh.get("co2") or fresh.get("eco2")
        if air is not None and (meaning := interpret(air)) is not None:
            parts.append(f"air {meaning.summary}")
        if poor:
            parts.append("check " + ", ".join(poor))
        elif fresh and not parts:
            parts.append("nothing needs attention")
        newest = max((r.time for r in fresh.values()), default=None)
        if newest is not None:
            parts.append(f"{newest:%H:%M}")
        if any(r.simulated for r in fresh.values()):
            parts.append("SIMULATED")
        self.room_card.set(value, " · ".join(parts) if parts else "No room readings.")

    def _refresh_status(self):
        status = self.backend.status()
        running = status.get("running", False)
        services = status.get("services", {})
        brain_ok = services.get("Brain") == "Running"

        state = self.backend.state
        startable = state in ("paused", "failed")
        self.pause_button.setText("▶  Start AURORA" if startable else "⏸  Pause AURORA")
        self.pause_button.setEnabled(state in ("running", "paused", "failed"))
        running_services = [name for name, s in services.items() if s == "Running"]
        stopped = [f"{name} {s.lower()}" for name, s in services.items() if s != "Running"]
        service_text = (", ".join(running_services) + " running" if running_services else "no services running") + \
            ("; " + ", ".join(stopped) if stopped else "")
        if state == "paused":
            self.ai_card.set("Paused", "Services stopped · model unloaded from RAM")
        elif not running:
            self.ai_card.set("Starting…", "Loading AURORA's kernel")
        elif brain_ok:
            self.ai_card.set("Online", service_text)
        else:
            self.ai_card.set("Degraded", service_text)
        self.ai_card.setToolTip("\n".join(f"{name}: {s}" for name, s in services.items())
                                + "".join(f"\nPlugin {n}: {s}" for n, s in status.get("plugins", {}).items()))

        ollama = self.backend.ollama
        model = status.get("model", settings.DEFAULT_MODEL)
        own = is_core_model(model)
        if ollama["ok"] is None:
            self.model_card.set(model, "Checking the model…" if own else "Checking Ollama…")
        elif ollama["ok"]:
            self.model_card.set(model, "AURORA's own model, on this PC" if own else "Ollama connected")
        else:
            self.model_card.set(model, f"{'AURORA model' if own else 'Ollama'}: {ollama['detail']}")

        tools = status.get("tools", {})
        if tools:
            ready = sum(1 for tool_state in tools.values() if tool_state == "Ready")
            self.tools_gauge.set(100 * ready / len(tools), f"{ready}/{len(tools)}",
                                 "Tools ready to use (the rest need a permission or failed to load)")
        else:
            self.tools_gauge.set(None)
        self._show_ai_state(self.backend.ai_state)

    def _refresh_slow(self):
        """Memory count and what's coming up (read off the GUI thread)."""
        engine = self.backend.engine
        if engine is None:
            return
        schedule = engine.tool_manager.get("Schedule")
        reminders = engine.tool_manager.get("Reminders")
        research = engine.tool_manager.get("Research")

        def work():
            count = len(engine.memory.memories())
            tasks = schedule.tasks.all() if schedule else []
            pending = reminders.scheduler.pending() if reminders else []
            log = getattr(research, "log", None)
            projects = log.projects() if log is not None else []
            return count, upcoming(pending, tasks, datetime.now()), projects

        def show(result):
            count, items, projects = result
            learning = "on" if self.backend.status().get("auto_memory") else "off"
            self.memory_card.set(str(count), f"memories stored · learning {learning}")
            self._show_upcoming(items)
            self._refresh_connections(projects)

        self.backend.run_io(work, show, lambda error: self.memory_card.set("Error", str(error)))

    def _show_upcoming(self, items):
        clear_layout(self.upcoming_rows)
        if not items:
            self.upcoming_rows.addWidget(label("No reminders or scheduled tasks. Try “remind me at 6 to call home”.",
                                               "Faint", wrap=True))
            return
        now = datetime.now()
        for moment, text, kind in items:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(label(when_text(moment, now), "Accent"))
            body = QLabel(text)
            body.setWordWrap(True)
            line.addWidget(body, 1)
            line.addWidget(label(kind, "Faint"))
            self.upcoming_rows.addWidget(row)

    def _show_system(self, sample: dict):
        if not sample:
            return
        cpu = sample.get("cpu")
        cores = sample.get("cores")
        self.cpu_gauge.set(cpu, None, f"{cores} logical cores" if cores else "")
        memory = sample.get("memory")
        if memory is not None:
            self.ram_gauge.set(memory.percent, None, f"{system_info.human_bytes(memory.used_bytes)} of "
                                                     f"{system_info.human_bytes(memory.total_bytes)} in use")
            process = sample.get("process")
            if process:
                share = 100 * process / memory.total_bytes
                self.process_gauge.set(share, system_info.human_bytes(process).replace(" ", ""),
                                       "Memory used by the Command Centre itself (Ollama's model is separate)")
        else:
            self.ram_gauge.set(None)

    def _refresh_activity(self):
        clear_layout(self.activity_rows)
        recent = list(self.backend.activities)[:3]
        if not recent:
            self.activity_rows.addWidget(label("Nothing yet. Activity appears here as AURORA works.", "Faint"))
        for activity in recent:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(StatusDot(activity.kind))
            line.addWidget(label(activity.time.strftime("%H:%M"), "Faint"))
            text = QLabel(activity.text)
            text.setWordWrap(True)
            line.addWidget(text, 1)
            self.activity_rows.addWidget(row)

    def set_accent(self, colour: str):
        self.orb.set_accent(colour)
        self.waveform.set_accent(colour)
        for gauge in (self.cpu_gauge, self.ram_gauge, self.process_gauge, self.tools_gauge):
            gauge.set_accent(colour)
