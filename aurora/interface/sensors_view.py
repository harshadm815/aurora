"""
Sensor Centre. Every reading shows where it came from, when it was
measured, whether it is LIVE, SIMULATED or a stale LAST KNOWN value, and
what it means (from the reference bands in aurora/sensors/interpret.py).
A quantity no device reports shows NO SENSOR, never a made-up value.

- Background watching (off by default): room sensors read every minute,
  readings kept for the history chart, and alerts when something becomes
  poor (shown here and as Windows notifications).
- History: the last 24 hours of any room quantity, with lowest, highest,
  average and trend.
- Health readings are fetched only when you ask, need their own
  permission, and are never interpreted.
"""

from datetime import datetime, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QListWidget, QPushButton,
                               QSizePolicy, QWidget)

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import STATE_COLOURS, Badge, Panel, StatusRow, discard, label
from aurora.interface.styles.theme import palette
from aurora.sensors.interpret import FAIR, GOOD, INFO, POOR, day_summary, interpret, trend
from aurora.sensors.reading import QUANTITIES, ROOM_QUANTITIES, Reading
from aurora.tool_plugins.sensors import HEALTH_NOTE

# Cards, in order. "co2" shows a true CO₂ sensor or, failing that, an SGP30's estimate;
# "pm2_5" also shows PM10 when a dust sensor reports it.
CARDS = ["temperature", "humidity", "pressure", "light", "motion", "co2", "tvoc", "pm2_5"]
ROOM = CARDS     # kept for older imports
HEALTH = ["heart_rate", "spo2"]
LEVEL_STATE = {GOOD: "ok", FAIR: "warning", POOR: "error", INFO: "info"}


def _age(reading) -> str:
    seconds = (datetime.now() - reading.time).total_seconds()
    if seconds < 60:
        return f"{reading.time:%H:%M:%S} · just now"
    return f"{reading.time:%H:%M:%S} · {int(seconds // 60)} min ago"


def readings_from_event(data: dict) -> tuple[dict[str, Reading], set[str]]:
    """Readings from a sensor.readings event (as published by the Sensor Service)."""
    readings, stale = {}, set()
    for quantity, item in (data.get("readings") or {}).items():
        if quantity not in QUANTITIES or not isinstance(item, dict):
            continue
        try:
            time = datetime.fromisoformat(item["time"])
        except (KeyError, TypeError, ValueError):
            continue
        readings[quantity] = Reading(quantity, item.get("value"), str(item.get("source", "")), "room",
                                     bool(item.get("simulated")), time)
        if item.get("stale"):
            stale.add(quantity)
    return readings, stale


class ReadingCard(Panel):

    def __init__(self, title: str):
        super().__init__(title, card=True)
        self.badge = self.add(Badge("NO DATA", "off"))
        self.value = self.add(label("—", "BigValue"))
        self.meaning = self.add(label("", "Faint", wrap=True))
        self.detail = self.add(label("", "Faint", wrap=True))

    def show(self, reading, stale: bool, extra: str = ""):
        self.value.setText(reading.formatted())
        if reading.simulated:
            self.badge.set("SIMULATED", "simulated")
        elif stale:
            self.badge.set("LAST KNOWN", "warning")
        else:
            self.badge.set("LIVE", "ok")
        meaning = interpret(reading)
        if meaning is not None:
            colour = STATE_COLOURS[LEVEL_STATE[meaning.level]]
            self.meaning.setText(meaning.summary[:1].upper() + meaning.summary[1:])
            self.meaning.setStyleSheet(f"color: {colour};")
            self.meaning.setToolTip(meaning.reference + (f"\n{meaning.advice}" if meaning.advice else ""))
        else:
            self.meaning.setText(reading.spec.note.capitalize() if reading.spec.note else "")
            self.meaning.setStyleSheet("")
        note = f" · {reading.spec.note}" if reading.spec.note and meaning is not None else ""
        self.detail.setText(f"{reading.source} · {_age(reading)}{note}{extra}")

    def empty(self, text: str, badge: str = "NO DATA"):
        self.value.setText("—")
        self.badge.set(badge, "off")
        self.meaning.setText("")
        self.meaning.setStyleSheet("")
        self.detail.setText(text)


class HistoryChart(QWidget):
    """Readings against time (last 24 hours). Simulated points are violet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.points: list[Reading] = []
        self.unit = ""
        self.decimals = 1
        self.message = "No history yet."

    def set_readings(self, readings: list[Reading], message: str = ""):
        self.points = [r for r in readings if not isinstance(r.value, bool)]
        if self.points:
            spec = self.points[0].spec
            self.unit, self.decimals = spec.unit, spec.decimals
        self.message = message or "No history yet."
        self.update()

    def paintEvent(self, event):
        p = palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(p.surface))
        if len(self.points) < 2:
            painter.setPen(QColor(p.text_faint))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        t0, t1 = self.points[0].time, self.points[-1].time
        values = [float(r.value) for r in self.points]
        low, high = min(values), max(values)
        pad = (high - low) * 0.1 or max(abs(high) * 0.05, 0.5)
        y0, y1 = low - pad, high + pad
        span = (t1 - t0).total_seconds() or 1
        area = QRectF(64, 14, self.width() - 80, self.height() - 44)

        def point(reading):
            x = area.left() + (reading.time - t0).total_seconds() / span * area.width()
            y = area.bottom() - (float(reading.value) - y0) / ((y1 - y0) or 1) * area.height()
            return QPointF(x, y)

        small = QFont(self.font())
        small.setPointSizeF(max(7.5, small.pointSizeF() - 1))
        painter.setFont(small)
        for i in range(5):
            value = y0 + (y1 - y0) * i / 4
            y = area.bottom() - i / 4 * area.height()
            painter.setPen(QPen(QColor(p.border)))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(QColor(p.text_faint))
            painter.drawText(QRectF(0, y - 8, 58, 16), Qt.AlignRight | Qt.AlignVCenter,
                             f"{value:.{self.decimals}f}")
        for i in range(5):
            moment = t0 + timedelta(seconds=span * i / 4)
            x = area.left() + i / 4 * area.width()
            painter.drawText(QRectF(x - 40, area.bottom() + 6, 80, 16), Qt.AlignCenter, f"{moment:%H:%M}")

        accent = QColor(p.accent)
        path = QPainterPath(point(self.points[0]))
        for reading in self.points[1:]:
            path.lineTo(point(reading))
        painter.setPen(QPen(accent, 2))
        painter.drawPath(path)
        for reading in self.points:
            painter.setBrush(QColor(p.violet if reading.simulated else p.accent))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(point(reading), 2.5, 2.5)


class SensorsView(BaseView):
    title = "Sensor Centre"
    subtitle = ("Room conditions with what they mean, and (optional) health readings. "
                "Simulated values are always marked; nothing is ever made up.")
    nav_icon = "🌡"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)

        controls = QHBoxLayout()
        refresh = QPushButton("Refresh readings")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(self.refresh)
        self.auto = QCheckBox("Refresh every 30 seconds")
        self.auto.toggled.connect(lambda on: self._timer.start(30_000) if on else self._timer.stop())
        self.watch = QCheckBox("Watch my room in the background")
        self.watch.setToolTip("Reads room sensors every minute (never health sensors), keeps them for the "
                              "history chart, and alerts you when something becomes poor.")
        self.watch.setChecked(bool(settings.SENSOR_MONITOR))
        self.watch.toggled.connect(self._toggle_watch)
        controls.addWidget(refresh)
        controls.addWidget(self.auto)
        controls.addWidget(self.watch)
        controls.addStretch(1)
        self.updated = label("", "Faint")
        controls.addWidget(self.updated)
        self.body.addLayout(controls)
        self.watch_status = label("", "Faint", wrap=True)
        self.body.addWidget(self.watch_status)

        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards: dict[str, ReadingCard] = {}
        extra = [q for q in ROOM_QUANTITIES if QUANTITIES[q].custom]
        for index, quantity in enumerate(CARDS + extra):
            title = "CO₂" if quantity == "co2" else QUANTITIES[quantity].label
            self.cards[quantity] = ReadingCard(title)
            self.cards[quantity].empty("No reading yet.")
            grid.addWidget(self.cards[quantity], index // 4, index % 4)
        self.body.addLayout(grid)

        middle = QHBoxLayout()
        history = Panel("History (last 24 hours)")
        chooser = QHBoxLayout()
        self.history_choice = QComboBox()
        for quantity in [q for q in ROOM_QUANTITIES if q != "motion"]:
            self.history_choice.addItem(QUANTITIES[quantity].label, quantity)
        self.history_choice.currentIndexChanged.connect(lambda _: self.refresh_history())
        chooser.addWidget(self.history_choice)
        chooser.addStretch(1)
        history.add_layout(chooser)
        self.chart = HistoryChart()
        history.add(self.chart, 1)
        self.history_text = history.add(label("", "Faint", wrap=True))
        middle.addWidget(history, 3)

        side = Panel("Alerts and connection")
        self.alert_list = QListWidget()
        self.alert_list.setMinimumHeight(90)
        self.alert_list.setWordWrap(True)
        self.alert_list.addItem("No alerts. Turn on background watching to get them.")
        side.add(self.alert_list)
        self.notes = side
        self.source_rows = []
        self.problems = side.add(label("", "Faint", wrap=True))
        middle.addWidget(side, 2)
        self.body.addLayout(middle, 1)

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

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        backend.sensor_readings.connect(self._watched)
        backend.sensor_alert.connect(self._alert)
        backend.sensor_monitoring.connect(self._watch_changed)
        self._show_watch_status()

    def on_engine_ready(self):
        self._show_sources()
        self._load_alerts()
        self._show_watch_status()

    def on_shown(self):
        self._show_sources()
        self._show_watch_status()
        if self.backend.engine is not None and not any(c.value.text() != "—" for c in self.cards.values()):
            self.refresh()
        self.refresh_history()

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
            if source.get("quantities"):
                text += " · only " + ", ".join(source["quantities"])
            row = StatusRow(source.get("name", kind), text, state)
            self.notes.layout_.insertWidget(len(self.source_rows) + 2, row)
            self.source_rows.append(row)

    # --- Background watching -----------------------------------------------------------

    def _toggle_watch(self, on: bool):
        problem = self.backend.set_sensor_monitoring(on)
        if problem and on:
            self.watch.blockSignals(True)
            self.watch.setChecked(False)
            self.watch.blockSignals(False)
            self.watch_status.setText(problem)
            return
        self._show_watch_status(on)

    def _watch_changed(self, on: bool):
        self.watch.blockSignals(True)
        self.watch.setChecked(on)
        self.watch.blockSignals(False)
        self._show_watch_status(on)

    def _show_watch_status(self, on: bool | None = None):
        service = self.backend.sensor_service()
        if on is None:
            on = bool(service and service.monitoring)
        if on:
            last = service.last_poll if service else None
            when = f"; last reading {last:%H:%M:%S}" if last else ""
            self.watch_status.setText(f"Watching: room sensors are read every {settings.SENSOR_MONITOR_SECONDS} s"
                                      f"{when}. Health sensors are never read in the background.")
        else:
            self.watch_status.setText("Background watching is off: readings are only taken when you ask.")

    def _watched(self, data: dict):
        readings, stale = readings_from_event(data)
        self._show_readings(readings, stale)
        self._show_watch_status(True)
        self.refresh_history()

    def _alert(self, alert: dict):
        if self.alert_list.count() == 1 and self.alert_list.item(0).text().startswith("No alerts"):
            self.alert_list.clear()
        stamp = str(alert.get("time", ""))[11:16]
        self.alert_list.insertItem(0, f"{stamp}  {alert.get('message', '')}")

    def _load_alerts(self):
        service = self.backend.sensor_service()
        for alert in reversed(list(service.alerts) if service else []):
            self._alert(alert)

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
        self.backend.run_io(lambda: tool.hub.snapshot(set(ROOM_QUANTITIES)), self._show,
                            lambda error: self.updated.setText(f"Reading failed: {error}"))

    def _show(self, snapshot):
        self._show_readings(snapshot.readings, snapshot.stale)
        self.problems.setText("\n".join(snapshot.problems) if snapshot.problems else "All configured sensors answered.")
        self.updated.setText(f"Updated {datetime.now():%H:%M:%S}")
        self.refresh_history()

    def _show_readings(self, readings: dict, stale: set):
        for quantity, card in self.cards.items():
            reading = readings.get(quantity)
            if reading is None and quantity == "co2":
                reading = readings.get("eco2")
            extra = ""
            if quantity == "pm2_5" and readings.get("pm10") is not None:
                extra = f" · PM10 {readings['pm10'].formatted()}"
            if reading is None:
                card.empty("No sensor reports this.", "NO SENSOR")
            else:
                card.show(reading, reading.quantity in stale, extra)

    def refresh_history(self):
        tool = self.backend.tool("Sensors")
        quantity = self.history_choice.currentData()
        if tool is None or quantity is None:
            return
        since = datetime.now() - timedelta(hours=24)

        def work():
            readings = tool.hub.store.history(quantity, since)
            if quantity == "co2" and not readings:
                readings = tool.hub.store.history("eco2", since)
            return readings

        self.backend.run_io(work, self._show_history)

    def _show_history(self, readings):
        label_ = self.history_choice.currentText().lower()
        self.chart.set_readings(readings, f"No stored {label_} readings in the last 24 hours. "
                                          "Turn on background watching to collect them.")
        lines = []
        summary = day_summary(readings)
        if summary is not None:
            lines.append(summary.sentence())
        change = trend(readings, readings[-1].quantity) if readings else None
        if change is not None:
            lines.append(change.sentence())
        self.history_text.setText(" ".join(lines))

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
