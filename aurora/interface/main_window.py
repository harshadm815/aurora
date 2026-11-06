"""The AURORA Command Centre window: sidebar navigation and pages."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QMainWindow, QPushButton, QStackedWidget, QStatusBar,
    QVBoxLayout, QWidget,
)

from aurora.config import settings
from aurora.interface.chat import ChatView
from aurora.interface.components.widgets import Emblem, emblem_pixmap, fade_in, label
from aurora.interface.dashboard import DashboardView
from aurora.interface.knowledge_view import KnowledgeView
from aurora.interface.memory_view import MemoryView
from aurora.interface.sensors_view import SensorsView
from aurora.interface.settings_view import SettingsView
from aurora.interface.styles.theme import palette, stylesheet
from aurora.interface.system_monitor import SystemMonitorView
from aurora.interface.tools_view import ToolsView
from aurora.interface.vision_view import VisionView

logger = logging.getLogger(__name__)

# (navigation name, view class)
PAGES = [
    ("Dashboard", DashboardView),
    ("Chat", ChatView),
    ("Memory", MemoryView),
    ("Knowledge", KnowledgeView),
    ("Tools", ToolsView),
    ("Vision", VisionView),
    ("Sensors", SensorsView),
    ("System", SystemMonitorView),
    ("Settings", SettingsView),
]


class MainWindow(QMainWindow):

    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.setWindowTitle("AURORA Command Centre")
        self.setMinimumSize(1100, 720)
        self.resize(1360, 860)

        root = QWidget()
        root.setObjectName("Root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 22, 0, 16)
        side.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(18, 0, 18, 18)
        self.emblem = Emblem(34)
        brand.addWidget(self.emblem)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("AURORA", "Brand"))
        names.addWidget(label("COMMAND CENTRE", "BrandSub"))
        brand.addLayout(names)
        brand.addStretch(1)
        side.addLayout(brand)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for name, view_class in PAGES:
            view = view_class(backend)
            self.pages[name] = view
            self.stack.addWidget(view)
            button = QPushButton(f"  {view.nav_icon}   {view.title.replace('&', '&&')}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, n=name: self.open_page(n))
            group.addButton(button)
            self.buttons[name] = button
            side.addWidget(button)
        side.addStretch(1)
        side.addWidget(label(f"   v{settings.VERSION} · local", "Faint"))

        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Starting AURORA…")

        self.pages["Dashboard"].navigate.connect(self.open_page)
        backend.engine_ready.connect(self._ready)
        backend.engine_failed.connect(lambda message: self.status.showMessage(message))
        backend.ollama_checked.connect(lambda _: self._update_status())
        backend.preferences_changed.connect(self.apply_preferences)

        self.current: str | None = None
        self.apply_preferences(backend.preferences)
        start = backend.preferences.get("start_page", "Dashboard")
        self.open_page(start if start in self.pages else "Dashboard")

    # --- Navigation -------------------------------------------------------------------

    def open_page(self, name: str):
        if name not in self.pages or name == self.current:
            self.buttons.get(name, QPushButton()).setChecked(True)
            return
        if self.current is not None:
            try:
                self.pages[self.current].on_hidden()
            except Exception:
                logger.exception("Leaving %s failed", self.current)
        self.current = name
        view = self.pages[name]
        self.stack.setCurrentWidget(view)
        self.buttons[name].setChecked(True)
        if self.backend.preferences.get("animations", True):
            fade_in(view)
        try:
            view.on_shown()
        except Exception:
            logger.exception("Opening %s failed", name)

    # --- State ------------------------------------------------------------------------

    def _ready(self):
        self._update_status()
        if self.current:
            self.pages[self.current].on_shown()

    def _update_status(self):
        status = self.backend.status()
        ollama = self.backend.ollama
        parts = ["AURORA online" if status.get("running") else "AURORA starting"]
        parts.append(f"model {status.get('model', settings.DEFAULT_MODEL)}")
        if ollama["ok"] is True:
            parts.append("Ollama connected")
        elif ollama["ok"] is False:
            parts.append("Ollama unavailable")
        self.status.showMessage("   ·   ".join(parts))

    def apply_preferences(self, values: dict):
        colours = palette(values.get("accent", "cyan"))
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(colours))
            app.setWindowIcon(QIcon(emblem_pixmap(64, colours.accent)))
        self.emblem.set_accent(colours.accent)
        self.pages["Dashboard"].set_accent(colours.accent)

    def closeEvent(self, event):
        for view in self.pages.values():
            try:
                view.on_hidden()   # Turns the camera off, stops timers.
            except Exception:
                logger.exception("Closing a page failed")
        self.status.showMessage("Shutting down AURORA…")
        self.backend.shutdown()
        event.accept()
