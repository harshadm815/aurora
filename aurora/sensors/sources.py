"""
Where readings come from (the device interface).

- ``HttpSource``: an ESP32 (or any device) on the local network serving
  JSON (see firmware/esp32_sensor_hub). AURORA asks it when you ask a
  question, or every minute while background watching is on.
- ``SimulatedSource``: plausible fake values for building and testing
  without hardware. Every simulated reading is marked as simulated, and
  AURORA says so in its answers.

Device JSON (``null`` = sensor not connected, warming up, or no finger on it):

    {"device": "aurora-sensors",
     "readings": {"temperature": 23.4, "humidity": 55.0, "pressure": 1008.2,
                  "light": 312, "motion": false, "eco2": 640, "tvoc": 85,
                  "heart_rate": null, "spo2": null},
     "ages": {"eco2": 1, "tvoc": 1},          optional: seconds since each value was measured
     "last_motion_s": 42}                     optional: seconds since motion was last seen

Timestamps: a reading's time is when it was *measured*: the moment it
arrived, minus its age if the device gives one. Values older than
``SENSOR_MAX_SAMPLE_AGE_SECONDS`` are refused, not shown as current.

Permitted readings: a source entry may list ``"quantities"``; anything
else that device sends is ignored.
"""

import logging
import math
import random
import time
from datetime import datetime, timedelta

import httpx

from aurora.config import settings
from aurora.sensors.reading import QUANTITIES, Reading, validate

logger = logging.getLogger(__name__)


class SourceError(Exception):
    """A source couldn't be read; the message is shown to the user."""


def _seconds(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return None
    return float(value)


class SensorSource:

    def __init__(self, name: str, location: str = "room", quantities: list[str] | None = None):
        self.name = name
        self.location = location
        self.allowed = set(quantities) if quantities else None    # None: everything known

    def read(self) -> tuple[list[Reading], list[str]]:
        """Valid readings, and notes about rejected values."""
        raise NotImplementedError

    def permits(self, quantity: str) -> bool:
        return self.allowed is None or quantity in self.allowed

    def _readings(self, values: dict, simulated: bool = False, ages: dict | None = None,
                  now: datetime | None = None) -> tuple[list[Reading], list[str]]:
        now = now or datetime.now()
        ages = ages if isinstance(ages, dict) else {}
        readings, problems = [], []
        for quantity, value in values.items():
            if value is None or quantity not in QUANTITIES or not self.permits(quantity):
                continue
            label = QUANTITIES[quantity].lower_label
            ok, reason = validate(quantity, value)
            if not ok:
                problems.append(f"{self.name}: ignored {label} ({reason})")
                continue
            measured = now
            if quantity in ages:
                age = _seconds(ages[quantity])
                if age is None:
                    problems.append(f"{self.name}: ignored {label} (it sent an invalid age)")
                    continue
                if age > settings.SENSOR_MAX_SAMPLE_AGE_SECONDS:
                    problems.append(f"{self.name}: ignored {label} (measured {age / 60:.0f} minutes ago, too old)")
                    continue
                measured = now - timedelta(seconds=age)
            readings.append(Reading(quantity, value, self.name, self.location, simulated, measured))
        return readings, problems


class HttpSource(SensorSource):

    def __init__(self, name: str, url: str, location: str = "room", client: httpx.Client | None = None,
                 quantities: list[str] | None = None):
        super().__init__(name, location, quantities)
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"Sensor URL for {name} must start with http:// or https://")
        self.url = url
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=settings.SENSOR_TIMEOUT_SECONDS)
        return self._client

    def read(self):
        try:
            response = self.client.get(self.url)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            raise SourceError(f"{self.name} didn't answer in time.") from None
        except httpx.HTTPError:
            raise SourceError(f"I can't reach {self.name}. Is it powered and on the same Wi-Fi?") from None
        except ValueError:
            raise SourceError(f"{self.name} sent data I couldn't read.") from None

        values = data.get("readings") if isinstance(data, dict) else None
        if not isinstance(values, dict):
            raise SourceError(f"{self.name} sent data in an unexpected format.")
        now = datetime.now()
        readings, problems = self._readings(values, ages=data.get("ages"), now=now)

        # When motion was last seen: kept in history (not a current reading).
        if "last_motion_s" in data and data["last_motion_s"] is not None and self.permits("motion"):
            age = _seconds(data["last_motion_s"])
            if age is None:
                problems.append(f"{self.name}: ignored the time of the last motion (invalid)")
            elif age <= 24 * 3600:
                readings.append(Reading("motion", True, self.name, self.location, False,
                                        now - timedelta(seconds=age)))
        return readings, problems


class SimulatedSource(SensorSource):
    """Slowly drifting, plausible room and health values."""

    def __init__(self, name: str = "Simulator", location: str = "room", seed: int | None = None,
                 clock=time.time, quantities: list[str] | None = None):
        super().__init__(name, location, quantities)
        self._random = random.Random(seed)
        self._clock = clock

    def read(self):
        t = self._clock() / 600.0   # One slow cycle every ~an hour.
        r = self._random
        hour = datetime.fromtimestamp(self._clock()).hour + datetime.fromtimestamp(self._clock()).minute / 60
        daylight = max(0.0, math.sin((hour - 6) / 12 * math.pi))      # 0 at night, 1 at noon
        values = {
            "temperature": round(24 + 2 * math.sin(t) + r.uniform(-0.2, 0.2), 1),
            "humidity": round(55 + 8 * math.cos(t) + r.uniform(-1, 1)),
            "pressure": round(1009 + 3 * math.sin(t / 3) + r.uniform(-0.5, 0.5), 1),
            "light": round(20 + 600 * daylight + r.uniform(0, 15)),
            "motion": r.random() < 0.3,
            "co2": round(800 + 300 * math.sin(t / 8) + r.uniform(-30, 30)),      # 470-1130 ppm, slow
            "tvoc": round(160 + 120 * math.sin(t / 8) + r.uniform(0, 20)),
            "pm2_5": round(12 + 8 * math.cos(t / 4) + r.uniform(0, 3)),
            "heart_rate": round(72 + 6 * math.sin(t * 2) + r.uniform(-2, 2)),
            "spo2": round(97 + r.uniform(-1, 1.5)),
        }
        return self._readings(values, simulated=True)


def build_sources(config: list[dict] | None = None, client: httpx.Client | None = None) -> list[SensorSource]:
    """Creates the sources listed in settings.SENSOR_SOURCES."""
    sources = []
    for entry in settings.SENSOR_SOURCES if config is None else config:
        kind = entry.get("type")
        name = entry.get("name") or kind
        location = entry.get("location", "room")
        allowed = entry.get("quantities")
        if allowed is not None and (not isinstance(allowed, list) or not all(isinstance(q, str) for q in allowed)):
            raise ValueError(f"'quantities' for {name} must be a list of names.")
        if kind == "http":
            sources.append(HttpSource(name, entry["url"], location, client, allowed))
        elif kind == "simulated":
            sources.append(SimulatedSource(name, location, quantities=allowed))
        else:
            raise ValueError(f"Unknown sensor source type '{kind}' for {name}.")
    return sources
