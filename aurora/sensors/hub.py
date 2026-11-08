"""
The Sensor Hub: asks every configured source for readings, keeps the
valid ones, stores them, and falls back to a recent stored reading
(up to ``SENSOR_MAX_AGE_MINUTES`` old) if a device can't be reached.

Readings are fetched when you ask, and every minute while background
watching (aurora/sensors/service.py) is on. One snapshot runs at a time.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import timedelta

from aurora.config import settings
from aurora.sensors.reading import HEALTH_QUANTITIES, Reading, register_extra
from aurora.sensors.sources import SensorSource, SourceError, build_sources
from aurora.sensors.store import SensorStore

logger = logging.getLogger(__name__)


@dataclass
class Snapshot:
    readings: dict[str, Reading] = field(default_factory=dict)   # quantity -> newest reading
    stale: set[str] = field(default_factory=set)                  # quantities taken from history
    problems: list[str] = field(default_factory=list)

    def get(self, quantity: str) -> Reading | None:
        return self.readings.get(quantity)


class SensorHub:

    def __init__(self, sources: list[SensorSource] | None = None, store: SensorStore | None = None,
                 client=None):
        self._sources = sources
        self._store = store
        self._client = client   # HTTP client for ESP32 sources (tests inject a fake).
        # Other simple sensors declared in settings (bad entries are reported, not used).
        self.setup_problems = register_extra()
        self._lock = threading.Lock()     # the Command Centre and the monitor may ask at once

    @property
    def sources(self) -> list[SensorSource]:
        if self._sources is None:
            self._sources = build_sources(client=self._client)
        return self._sources

    @property
    def store(self) -> SensorStore:
        if self._store is None:
            self._store = SensorStore()
        return self._store

    def snapshot(self, quantities: set[str], include_health: bool = False) -> Snapshot:
        """Fresh readings for ``quantities``. Health quantities only if ``include_health``."""
        if not include_health:
            quantities = quantities - HEALTH_QUANTITIES
        with self._lock:
            return self._snapshot(quantities)

    def _snapshot(self, quantities: set[str]) -> Snapshot:

        snap = Snapshot()
        snap.problems.extend(self.setup_problems)
        for source in self.sources:
            try:
                readings, problems = source.read()
            except SourceError as error:
                snap.problems.append(str(error))
                continue
            except Exception as error:
                logger.exception("Sensor source failed")
                snap.problems.append(f"{source.name} failed ({type(error).__name__}).")
                continue

            snap.problems.extend(problems)
            wanted = [r for r in readings if r.quantity in quantities]
            self.store.add(wanted)
            for reading in wanted:      # the most recently measured value of each kind
                current = snap.readings.get(reading.quantity)
                if current is None or reading.time > current.time:
                    snap.readings[reading.quantity] = reading

        # Anything no device answered for: use a recent stored reading, if any.
        max_age = timedelta(minutes=settings.SENSOR_MAX_AGE_MINUTES)
        for quantity in quantities - set(snap.readings):
            reading = self.store.latest(quantity, quantity in HEALTH_QUANTITIES, max_age)
            if reading is not None:
                snap.readings[quantity] = reading
                snap.stale.add(quantity)

        logger.info("Sensor snapshot: %d readings, %d problems", len(snap.readings), len(snap.problems))
        return snap
