"""
AURORA in the Windows notification area (system tray).

Menu: Open AURORA · Start AURORA · Pause AURORA · Settings · Exit.
Double-click (or single click) the icon to open the window. The tooltip
and the enabled menu items follow AURORA's real state.
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

STATE_TEXT = {
    "starting": "starting…",
    "running": "online",
    "paused": "paused (model unloaded)",
    "failed": "couldn't start",
    "stopped": "stopped",
}


class Tray(QObject):

    open_requested = Signal()
    settings_requested = Signal()
    exit_requested = Signal()

    def __init__(self, backend, icon: QIcon, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.icon = QSystemTrayIcon(icon, parent)
        self._told_about_tray = False

        menu = QMenu()
        self.open_action = QAction("Open AURORA", menu)
        self.start_action = QAction("Start AURORA", menu)
        self.pause_action = QAction("Pause AURORA", menu)
        self.settings_action = QAction("Settings", menu)
        self.exit_action = QAction("Exit", menu)
        font = self.open_action.font()
        font.setBold(True)
        self.open_action.setFont(font)

        self.open_action.triggered.connect(self.open_requested)
        self.start_action.triggered.connect(backend.resume)
        self.pause_action.triggered.connect(backend.pause)
        self.settings_action.triggered.connect(self.settings_requested)
        self.exit_action.triggered.connect(self.exit_requested)

        menu.addAction(self.open_action)
        menu.addSeparator()
        menu.addAction(self.start_action)
        menu.addAction(self.pause_action)
        menu.addSeparator()
        menu.addAction(self.settings_action)
        menu.addSeparator()
        menu.addAction(self.exit_action)
        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)

        backend.state_changed.connect(self.update_state)
        self.update_state(backend.state)
        # Background sensor watching: alerts as Windows notifications (if switched on).
        self.notified: list[str] = []
        backend.sensor_alert.connect(self.sensor_alert)

    def sensor_alert(self, alert: dict):
        from aurora.config import settings

        if not settings.SENSOR_NOTIFICATIONS:
            return
        message = str(alert.get("message", ""))[:250]
        self.notified.append(message)
        self.icon.showMessage("AURORA: room sensors", message, QSystemTrayIcon.Warning, 8000)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self):
        self.icon.show()

    def hide(self):
        self.icon.hide()

    def update_state(self, state: str):
        self.icon.setToolTip(f"AURORA: {STATE_TEXT.get(state, state)}")
        self.start_action.setEnabled(state in ("paused", "failed"))
        self.pause_action.setEnabled(state == "running")

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_requested.emit()

    def tell_once(self):
        """The first time the window goes to the tray, say where AURORA went."""
        if not self._told_about_tray:
            self._told_about_tray = True
            self.icon.showMessage("AURORA is still running",
                                  "AURORA is in the notification area. Right-click its icon for options.",
                                  QSystemTrayIcon.Information, 4000)
