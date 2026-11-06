"""
Sensor Centre. Every reading shows where it came from, when, and whether
it is LIVE, SIMULATED or a stale last-known value. Health readings are
fetched only when you ask and need their own permission.
"""

from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QPushButton

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Badge, Panel, StatusRow, discard, label
from aurora.sensors.reading import QUANTITIES
from aurora.tool_plugins.sensors import HEALTH_NOTE

ROOM = ["temperature", "humidity", "pressure", "motion"]
HEALTH = ["heart_rate", "spo2"]


def _age(reading) -> str:
    seconds = (datetime.now() - reading.time).total_seconds()
    if seconds < 60:
        return f"{reading.time:%H:%M:%S} · just now"
    return f"{reading.time:%H:%M:%S} · {int(seconds // 60)} min ago"


class ReadingCard(Panel):

    def __init__(self, title: str):
        super().__init__(title, card=True)
        self.badge = self.add(Badge("NO DATA", "off"))
        self.value = self.add(label("—", "BigValue"))
        self.detail = self.add(label("", "Faint", wrap=True))

    def show(self, reading, stale: bool):
        self.value.setText(reading.formatted())
        if reading.simulated:
            self.badge.set("SIMULATED", "simulated")
        elif stale:
            self.badge.set("LAST KNOWN", "warning")
        else:
            self.badge.set("LIVE", "ok")
        self.detail.setText(f"{reading.source} · {_age(reading)}")

    def empty(self, text: str, badge: str = "NO DATA"):
        self.value.setText("—")
        self.badge.set(badge, "off")
        self.detail.setText(text)


class SensorsView(BaseView):
    title = "Sensor Centre"
    subtitle = "Room conditions and (optional) health readings. Simulated values are always marked."
    nav_icon = "🌡"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)

        controls = QHBoxLayout()
        refresh = QPushButton("Refresh readings")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(self.refresh)
        self.auto = QCheckBox("Refresh every 30 seconds")
        self.auto.toggled.connect(lambda on: self._timer.start(30_000) if on else self._timer.stop())
        controls.addWidget(refresh)
        controls.addWidget(self.auto)
        controls.addStretch(1)
        self.updated = label("", "Faint")
        controls.addWidget(self.updated)
        self.body.addLayout(controls)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.cards = {}
        for column, quantity in enumerate(ROOM):
            self.cards[quantity] = ReadingCard(QUANTITIES[quantity].label)
            grid.addWidget(self.cards[quantity], 0, column)
        self.air = ReadingCard("Air quality")
        self.air.empty("No air-quality sensor is supported yet (planned).", "NOT SUPPORTED")
        grid.addWidget(self.air, 0, len(ROOM))
        self.body.addLayout(grid)

        self.notes = Panel("Connection")
        self.source_rows = []
        self.problems = self.notes.add(label("", "Faint", wrap=True))
        self.body.addWidget(self.notes)

        health = Panel("Health (informational, not medical)")
        row = QHBoxLayout()
        self.health_button = QPushButton("Show health readings")
        self.health_button.clicked.connect(self.refresh_health)
        row.addWidget(self.health_button)
        row.addStretch(1)
        health.add_layout(row)
        health_grid = QGridLayout()
        self.health_cards = {}
        for column, quantity in enumerate(HEALTH):
            self.health_cards[quantity] = ReadingCard(QUANTITIES[quantity].label)
            self.health_cards[quantity].empty("Not fetched. Health readings are only read when you ask.")
            health_grid.addWidget(self.health_cards[quantity], 0, column)
        health.add_layout(health_grid)
        health.add(label(HEALTH_NOTE, "Faint", wrap=True))
        self.body.addWidget(health)
        self.body.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    def on_engine_ready(self):
        self._show_sources()

    def on_shown(self):
        self._show_sources()
        if self.backend.engine is not None and not any(c.value.text() != "—" for c in self.cards.values()):
            self.refresh()

    def on_hidden(self):
        self.auto.setChecked(False)

    def _show_sources(self):
        for row in self.source_rows:
            discard(row)
        self.source_rows = []
        for source in settings.SENSOR_SOURCES:
            kind = source.get("type")
            state, text = ("simulated", "Simulator (demonstration data)") if kind == "simulated" \
                else ("info", source.get("url", "network device"))
            row = StatusRow(source.get("name", kind), text, state)
            self.notes.layout_.insertWidget(len(self.source_rows) + 1, row)
            self.source_rows.append(row)

    # --- Reading ----------------------------------------------------------------------

    def _tool(self, name):
        tool = self.backend.tool(name)
        if tool is None:
            return None, f"The {name} tool isn't loaded."
        missing = self.backend.missing_permissions(name, tool.required_permissions)
        if missing:
            return None, f"{name} readings are switched off (needs {', '.join(p.value for p in missing)})."
        return tool, ""

    def refresh(self):
        tool, problem = self._tool("Sensors")
        if tool is None:
            for card in self.cards.values():
                card.empty(problem)
            return
        self.updated.setText("Reading sensors…")
        self.backend.run_io(lambda: tool.hub.snapshot(set(ROOM)), self._show,
                            lambda error: self.updated.setText(f"Reading failed: {error}"))

    def _show(self, snapshot):
        for quantity, card in self.cards.items():
            reading = snapshot.get(quantity)
            if reading is None:
                card.empty("No sensor reported this.")
            else:
                card.show(reading, quantity in snapshot.stale)
        self.problems.setText("\n".join(snapshot.problems) if snapshot.problems else "All configured sensors answered.")
        self.updated.setText(f"Updated {datetime.now():%H:%M:%S}")

    def refresh_health(self):
        tool, problem = self._tool("Health")
        if tool is None:
            for card in self.health_cards.values():
                card.empty(problem)
            return
        self.backend.run_io(lambda: tool.hub.snapshot(set(HEALTH), include_health=True), self._show_health)

    def _show_health(self, snapshot):
        for quantity, card in self.health_cards.items():
            reading = snapshot.get(quantity)
            if reading is None:
                card.empty("No reading. Place your fingertip on the sensor and try again.")
            else:
                card.show(reading, quantity in snapshot.stale)
