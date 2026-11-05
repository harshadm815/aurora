"""
Where readings come from.

- ``HttpSource``: an ESP32 on the local network serving JSON
  (see firmware/esp32_sensor_hub). AURORA asks it when you ask a question.
- ``SimulatedSource``: plausible fake values for building and testing
  without hardware. Every simulated reading is marked as simulated, and
  AURORA says so in its answers.

ESP32 JSON format (``null`` = sensor not connected or no finger on it):

    {"device": "aurora-sensors",
     "readings": {"temperature": 23.4, "humidity": 55.0, "pressure": 1008.2,
                  "motion": false, "heart_rate": null, "spo2": null}}
"""

import logging
import math
import random
import time

import httpx

from aurora.config import settings
from aurora.sensors.reading import QUANTITIES, Reading, validate

logger = logging.getLogger(__name__)


class SourceError(Exception):
    """A source couldn't be read; the message is shown to the user."""


class SensorSource:

    def __init__(self, name: str, location: str = "room"):
        self.name = name
        self.location = location

    def read(self) -> tuple[list[Reading], list[str]]:
        """Valid readings, and notes about rejected values."""
        raise NotImplementedError

    def _readings(self, values: dict, simulated: bool = False) -> tuple[list[Reading], list[str]]:
        readings, problems = [], []
        for quantity, value in values.items():
            if value is None or quantity not in QUANTITIES:
                continue
            ok, reason = validate(quantity, value)
            if not ok:
                problems.append(f"{self.name}: ignored {QUANTITIES[quantity].label.lower()} ({reason})")
                continue
            readings.append(Reading(quantity, value, self.name, self.location, simulated))
        return readings, problems


class HttpSource(SensorSource):

    def __init__(self, name: str, url: str, location: str = "room", client: httpx.Client | None = None):
        super().__init__(name, location)
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
        return self._readings(values)


class SimulatedSource(SensorSource):
    """Slowly drifting, plausible room and health values."""

    def __init__(self, name: str = "Simulator", location: str = "room", seed: int | None = None,
                 clock=time.time):
        super().__init__(name, location)
        self._random = random.Random(seed)
        self._clock = clock

    def read(self):
        t = self._clock() / 600.0   # One slow cycle every ~an hour.
        r = self._random
        values = {
            "temperature": round(24 + 2 * math.sin(t) + r.uniform(-0.2, 0.2), 1),
            "humidity": round(55 + 8 * math.cos(t) + r.uniform(-1, 1)),
            "pressure": round(1009 + 3 * math.sin(t / 3) + r.uniform(-0.5, 0.5), 1),
            "motion": r.random() < 0.3,
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
        if kind == "http":
            sources.append(HttpSource(name, entry["url"], location, client))
        elif kind == "simulated":
            sources.append(SimulatedSource(name, location))
        else:
            raise ValueError(f"Unknown sensor source type '{kind}' for {name}.")
    return sources
