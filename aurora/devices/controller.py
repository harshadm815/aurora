"""
The only path from a request to a device.

Every change is checked here, in this order:

1. The device exists in settings (by name or alias).
2. The action is one the device allows; values are integers within its range.
3. The device hasn't been switched too often (rate limit).

Then it is applied through the device's driver and recorded in the
audit trail. Confirmation with the user happens before this, in the
Devices tool.
"""

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from aurora.config import settings
from aurora.devices.device import Command, Device, DeviceError, DeviceState
from aurora.devices.drivers import Driver, HttpRelayDriver, SimulatedDriver

logger = logging.getLogger(__name__)

_ARTICLES = re.compile(r"^(?:the|my|our)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class AuditEntry:
    time: datetime
    device: str
    change: str
    simulated: bool


class DeviceController:

    def __init__(self, devices: list[Device], drivers: dict[str, Driver],
                 clock=time.monotonic, max_changes_per_minute: int | None = None):
        self.devices = {d.id: d for d in devices}
        self.drivers = drivers
        self.clock = clock
        self.max_changes = (settings.DEVICE_MAX_CHANGES_PER_MINUTE
                            if max_changes_per_minute is None else max_changes_per_minute)
        self._recent: dict[str, deque] = {}
        self.audit: list[AuditEntry] = []

    # --- Lookup -----------------------------------------------------------------

    def find(self, name: str) -> Device:
        wanted = _ARTICLES.sub("", (name or "").strip().lower())
        if not wanted:
            raise DeviceError("Which device?")

        exact = [d for d in self.devices.values() if wanted in d.names()]
        partial = [d for d in self.devices.values() if any(wanted in n or n in wanted for n in d.names())]
        matches = exact or partial

        if not matches:
            known = ", ".join(d.name for d in self.devices.values()) or "none are set up"
            raise DeviceError(f"I don't have a device called \"{name}\". Your devices: {known}.")
        if len(matches) > 1:
            raise DeviceError(f"Which one: {', '.join(d.name for d in matches)}?")
        return matches[0]

    def suggest(self, name: str) -> Device | None:
        """The one device whose name or alias is close to ``name`` ("desk life"
        → desk light, as speech recognition sometimes hears it), or None.
        A suggestion is never acted on without asking the user."""
        import difflib

        wanted = _ARTICLES.sub("", (name or "").strip().lower())
        if not wanted:
            return None
        names = {n: d for d in self.devices.values() for n in d.names()}
        # 0.7: "desk life" (0.74) finds the desk light; "fun" or "van" (0.67) don't become the fan.
        close = difflib.get_close_matches(wanted, list(names), n=3, cutoff=0.7)
        candidates = {names[n].id: names[n] for n in close}
        return next(iter(candidates.values())) if len(candidates) == 1 else None

    def driver(self, device: Device) -> Driver:
        return self.drivers[device.id]

    # --- Checks -----------------------------------------------------------------

    def validate(self, device: Device, action: str, value=None) -> Command:
        if action not in device.allowed:
            raise DeviceError(f"The {device.name} doesn't allow \"{action}\".")

        if action != "set":
            return Command(action)

        if isinstance(value, bool) or not isinstance(value, int):
            raise DeviceError(f"Tell me a whole number for the {device.name} {device.setting}.")
        low, high = device.range
        if not low <= value <= high:
            raise DeviceError(
                f"The {device.name} {device.setting} must be between {low} and {high}{device.unit}."
            )
        return Command("set", value)

    def _check_rate(self, device: Device) -> None:
        now = self.clock()
        recent = self._recent.setdefault(device.id, deque())
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= self.max_changes:
            raise DeviceError(
                f"The {device.name} has been switched {len(recent)} times in the last minute. "
                "Please wait a moment."
            )

    # --- Actions ----------------------------------------------------------------

    def state(self, device: Device) -> DeviceState:
        return self.driver(device).state(device)

    def apply(self, device: Device, command: Command) -> DeviceState:
        self._check_rate(device)
        state = self.driver(device).apply(device, command)
        self._recent[device.id].append(self.clock())

        entry = AuditEntry(datetime.now(), device.name, command.describe(device), self.driver(device).simulated)
        self.audit.append(entry)
        logger.info("Device change: %s (%s)", entry.change, "simulated" if entry.simulated else "real")
        return state


def build_controller(config: list[dict] | None = None, client=None) -> DeviceController:
    """Devices and drivers from settings.DEVICES."""
    devices, drivers = [], {}
    simulator = SimulatedDriver()
    relay = None

    for entry in settings.DEVICES if config is None else config:
        device = Device(
            id=entry["id"], name=entry["name"], kind=entry["kind"],
            aliases=entry.get("aliases", []), confirm=entry.get("confirm", True),
            allowed=set(entry["allowed"]) if entry.get("allowed") else None,
        )
        if entry.get("driver", "simulated") == "simulated":
            drivers[device.id] = simulator
        elif entry["driver"] == "esp32":
            relay = relay or HttpRelayDriver(settings.DEVICE_CONTROLLER_URL,
                                             settings.DEVICE_CONTROLLER_TOKEN, client)
            drivers[device.id] = relay
        else:
            raise ValueError(f"Unknown driver '{entry['driver']}' for {device.name}.")
        devices.append(device)

    return DeviceController(devices, drivers)
