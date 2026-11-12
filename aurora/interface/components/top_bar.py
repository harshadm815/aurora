"""
The bar along the top of every page: what AURORA is doing, the model,
internet and local-network access, CPU and RAM, and the time.

Every item is real state (the backend, the network gate, the system
sampler), never decoration. Clicking an item opens the page that explains
or changes it.
"""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from aurora.config import settings
from aurora.interface.components.core_orb import LABELS
from aurora.interface.components.widgets import StatusDot, label
from aurora.network import gate as network_gate
from aurora.providers.core_provider import is_core_model

AI_DOTS = {"idle": "ok", "thinking": "info", "responding": "info", "speaking": "info", "listening": "info",
           "processing": "warning", "error": "error", "offline": "off"}
INTERNET_TEXT = {"on": "Internet on", "ask": "Internet: asks first", "off": "Internet off"}


def internet_summary() -> tuple[str, str, str]:
    """(text, dot state, tooltip) for the internet chip, from the network gate."""
    mode = network_gate.mode
    approved = network_gate.approved()
    text = INTERNET_TEXT[mode]
    if mode == "ask" and approved:
        text += f" · {len(approved)} allowed"
    tooltip = {"on": "Services may go online without asking.",
               "ask": "Each service asks before it first goes online in a session.",
               "off": "Nothing goes to the internet."}[mode]
    if mode == "ask":
        tooltip += "\nAllowed this session: " + (", ".join(approved) if approved else "none yet")
    tooltip += "\nLocal network: " + ("on" if settings.LOCAL_NETWORK_ACCESS else "off")
    return text, {"on": "ok", "ask": "warning", "off": "off"}[mode], tooltip


class Chip(QPushButton):
    """A flat, clickable status item: a dot and a short text."""

    def __init__(self, text: str = "", state: str = "off", parent=None):
        super().__init__(parent)
        self.setObjectName("Chip")
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(6)
        self.dot = StatusDot(state)
        self.text_label = QLabel(text)
        self.text_label.setObjectName("ChipText")
        layout.addWidget(self.dot)
        layout.addWidget(self.text_label)
        self.setMinimumHeight(28)

    def set(self, text: str, state: str, pulse: bool = False, tooltip: str = "") -> None:
        self.text_label.setText(text)
        self.dot.set_state(state, pulse)
        self.setToolTip(tooltip)
        self.setMinimumWidth(self.text_label.sizeHint().width() + 40)


class TopBar(QFrame):

    navigate = Signal(str)

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.setObjectName("TopBar")
        self.setFixedHeight(46)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 18, 0)
        layout.setSpacing(6)

        self.page = label("", "TopBarTitle")
        layout.addWidget(self.page)
        layout.addStretch(1)
        self.ai = Chip()
        self.ai.clicked.connect(lambda: self.navigate.emit("Chat"))
        self.model = Chip()
        self.model.clicked.connect(lambda: self.navigate.emit("Models"))
        self.internet = Chip()
        self.internet.clicked.connect(lambda: self.navigate.emit("Settings"))
        self.system = Chip()
        self.system.clicked.connect(lambda: self.navigate.emit("System"))
        for chip in (self.ai, self.model, self.internet, self.system):
            layout.addWidget(chip)
        separator = QWidget()
        separator.setFixedWidth(10)
        layout.addWidget(separator)
        self.clock = label("", "TopBarClock")
        self.clock.setToolTip("")
        layout.addWidget(self.clock)

        backend.ai_state_changed.connect(lambda _: self.show_ai())
        backend.ollama_checked.connect(lambda _: self.show_model())
        backend.state_changed.connect(lambda _: self.show_model())
        backend.preferences_changed.connect(lambda _: (self.show_model(), self.show_internet()))
        backend.system_sampled.connect(self.show_system)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.refresh()

    def refresh(self) -> None:
        self.show_ai()
        self.show_model()
        self.show_internet()
        self.show_system(self.backend.last_sample)
        self._tick()

    def set_page(self, title: str) -> None:
        self.page.setText(title)

    def show_ai(self) -> None:
        state = getattr(self.backend, "ai_state", "processing")
        busy = state in ("thinking", "responding", "speaking", "listening", "processing")
        self.ai.set(f"AURORA · {LABELS.get(state, state)}", AI_DOTS.get(state, "off"), pulse=busy or state == "idle",
                    tooltip="What AURORA is doing now. Click to open Chat.")

    def show_model(self) -> None:
        status = self.backend.status() if self.backend.engine is not None else {}
        model = status.get("model", settings.DEFAULT_MODEL)
        ok = self.backend.ollama.get("ok")
        own = is_core_model(model)
        state = "ok" if ok else "warning" if ok is None else "error"
        where = "AURORA's own model" if own else "Ollama"
        detail = {True: "ready", None: "checking", False: f"unavailable: {self.backend.ollama.get('detail', '')}"}[ok]
        self.model.set(model, state, tooltip=f"Language model ({where}): {detail}. Click for the Model manager.")

    def show_internet(self) -> None:
        text, state, tooltip = internet_summary()
        self.internet.set(text, state, tooltip=tooltip + "\nClick to change it in Settings.")

    def show_system(self, sample: dict) -> None:
        if not sample:
            self.system.set("CPU — · RAM —", "off")
            return
        cpu = sample.get("cpu")
        memory = sample.get("memory")
        ram = memory.percent if memory is not None else None
        worst = max(v for v in (cpu or 0, ram or 0))
        state = "error" if worst >= 90 else "warning" if worst >= 75 else "ok"
        text = f"CPU {cpu:.0f}% · RAM {ram:.0f}%" if cpu is not None and ram is not None else "System"
        self.system.set(text, state, tooltip="This PC's processor and memory use. Click for the System monitor.")

    def _tick(self) -> None:
        now = datetime.now()
        self.clock.setText(now.strftime("%H:%M"))
        self.clock.setToolTip(now.strftime("%A, %d %B %Y"))
        # The gate has no signal; approvals change rarely, so the clock tick picks them up.
        if now.second % 5 == 0:
            self.show_internet()
