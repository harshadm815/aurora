"""
The Sensor Service: the link between sensor devices and the rest of AURORA.

    Physical sensor -> device interface (sources.py) -> Sensor Hub -> SensorService
        -> event bus (sensor.readings, sensor.alert) -> Command Centre, notifications

Readings are always available on request (through the Sensors tool and
the Sensor Centre). **Background watching** is extra and off by default
(``SENSOR_MONITOR``, or the switch in the Sensor Centre): while it is on,
room sensors are read every ``SENSOR_MONITOR_SECONDS`` and

- every snapshot is published as ``sensor.readings`` (values with their
  measuring time, source and whether they are simulated or stale);
- a reading that falls in a *poor* band (aurora/sensors/interpret.py)
  is published as ``sensor.alert``, at most once per
  ``SENSOR_ALERT_COOLDOWN_MINUTES`` for each quantity. Stale values
  (from history, because a device didn't answer) never raise alerts.

Watching needs the ``sensors`` permission, like the Sensors tool, and
never reads health sensors. Values aren't logged; only counts.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime

from aurora.config import settings
from aurora.events import topics
from aurora.kernel.lifecycle import ServiceState
from aurora.kernel.service import BaseService
from aurora.sensors.hub import SensorHub, Snapshot
from aurora.sensors.interpret import POOR, interpret
from aurora.sensors.reading import QUANTITIES, ROOM_QUANTITIES

logger = logging.getLogger(__name__)

SOURCE = "SensorService"


class SensorService(BaseService):

    def __init__(self, hub: SensorHub, events, permitted: Callable[[], bool] = lambda: True,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__("Sensors")
        self.hub = hub
        self.events = events
        self.permitted = permitted
        self.clock = clock
        self.alerts: deque[dict] = deque(maxlen=50)     # recent alerts, newest first
        self.last_snapshot: Snapshot | None = None
        self.last_poll: datetime | None = None
        self._last_alert: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- Service lifecycle -------------------------------------------------------------

    def start(self):
        self.state = ServiceState.RUNNING
        if settings.SENSOR_MONITOR:
            problem = self.start_monitoring()
            if problem:
                logger.info("Background sensor watching not started: %s", problem)

    def stop(self):
        self.stop_monitoring()
        self.state = ServiceState.STOPPED

    # --- Background watching -------------------------------------------------------------

    @property
    def monitoring(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_monitoring(self) -> str | None:
        """Starts watching; returns why it can't, or None."""
        if not self.permitted():
            return "AURORA isn't allowed to read sensors (the Sensors permission is off)."
        with self._lock:
            if self.monitoring:
                return None
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="aurora-sensor-monitor", daemon=True)
            self._thread.start()
        self.events.emit(topics.SENSOR_MONITORING, {"on": True, "every_seconds": settings.SENSOR_MONITOR_SECONDS},
                         source=SOURCE)
        logger.info("Background sensor watching on (every %d s)", settings.SENSOR_MONITOR_SECONDS)
        return None

    def stop_monitoring(self) -> None:
        with self._lock:
            thread, self._thread = self._thread, None
            self._stop.set()
        if thread is not None:
            thread.join(timeout=settings.SENSOR_TIMEOUT_SECONDS * 2 + 2)
            self.events.emit(topics.SENSOR_MONITORING, {"on": False}, source=SOURCE)
            logger.info("Background sensor watching off")

    def _run(self):
        while not self._stop.is_set():
            if not self.permitted():          # permission taken away while running
                logger.info("Background sensor watching stopped: no permission")
                break
            try:
                self.poll()
            except Exception:
                logger.exception("Sensor watching failed; trying again next time")
            if self._stop.wait(settings.SENSOR_MONITOR_SECONDS):
                break

    # --- One round -------------------------------------------------------------------------

    def poll(self) -> Snapshot:
        """Reads the room sensors once, publishes the readings and any alerts."""
        snapshot = self.hub.snapshot(set(ROOM_QUANTITIES))
        self.last_snapshot = snapshot
        self.last_poll = datetime.now()
        self.events.emit(topics.SENSOR_READINGS, self.describe(snapshot), source=SOURCE)
        for alert in self.check_alerts(snapshot):
            self.alerts.appendleft(alert)
            self.events.emit(topics.SENSOR_ALERT, alert, source=SOURCE)
        logger.info("Sensor watch: %d readings, %d problems", len(snapshot.readings), len(snapshot.problems))
        return snapshot

    @staticmethod
    def describe(snapshot: Snapshot) -> dict:
        return {
            "readings": {
                quantity: {"value": reading.value, "time": reading.time.isoformat(timespec="seconds"),
                           "source": reading.source, "simulated": reading.simulated,
                           "stale": quantity in snapshot.stale}
                for quantity, reading in snapshot.readings.items()
            },
            "problems": len(snapshot.problems),
            "simulated": any(r.simulated for r in snapshot.readings.values()),
        }

    def check_alerts(self, snapshot: Snapshot) -> list[dict]:
        alerts = []
        cooldown = settings.SENSOR_ALERT_COOLDOWN_MINUTES * 60
        now = self.clock()
        for quantity, reading in snapshot.readings.items():
            if quantity in snapshot.stale:
                continue
            meaning = interpret(reading)
            if meaning is None or meaning.level != POOR:
                continue
            last = self._last_alert.get(quantity)
            if last is not None and now - last < cooldown:
                continue
            self._last_alert[quantity] = now
            label = QUANTITIES[quantity].label
            message = f"{label} in your {reading.location} is {reading.formatted()}: {meaning.summary}."
            if meaning.advice:
                message += f" {meaning.advice}"
            if reading.simulated:
                message += " (Simulated reading, not your real room.)"
            alerts.append({"quantity": quantity, "level": meaning.level, "message": message,
                           "value": reading.value, "source": reading.source, "simulated": reading.simulated,
                           "time": reading.time.isoformat(timespec="seconds")})
        return alerts
