"""
Sensor tools.

- Sensors: room readings (temperature, humidity, air pressure, motion).
- Health:  heart rate and blood oxygen from a fingertip sensor. A separate
  tool with its own ``health_data`` permission, so it can be switched off
  on its own. Readings are informational, never medical advice.

Simulated values are always labelled as simulated.
"""

from datetime import datetime

from aurora.kernel.permissions import Permission
from aurora.sensors.hub import SensorHub, Snapshot
from aurora.sensors.reading import HEALTH_QUANTITIES, QUANTITIES, Reading
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

ROOM_QUANTITIES = ["temperature", "humidity", "pressure", "motion"]
HEALTH_ORDER = ["heart_rate", "spo2"]

SIMULATED_NOTE = "(Simulated values: no real sensor is connected yet.)"
HEALTH_NOTE = (
    "This is informational only, not a medical measurement. "
    "If you feel unwell, please contact a doctor."
)


def _age(reading: Reading, now: datetime | None = None) -> str:
    seconds = ((now or datetime.now()) - reading.time).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''} ago"


def _line(reading: Reading, stale: bool) -> str:
    when = _age(reading) + (", last known value" if stale else "")
    return f"{reading.spec.label}: {reading.formatted()} ({reading.source}, {when})"


def _report(snapshot: Snapshot, order: list[str], missing_hint: str) -> tuple[list[str], bool]:
    lines, simulated = [], False
    for quantity in order:
        reading = snapshot.get(quantity)
        if reading is None:
            lines.append(f"{QUANTITIES[quantity].label}: no reading. {missing_hint}")
            continue
        simulated = simulated or reading.simulated
        lines.append(_line(reading, quantity in snapshot.stale))
    return lines, simulated


class SensorsTool(BaseTool):

    required_permissions = (Permission.SENSORS,)

    def __init__(self, hub: SensorHub):
        super().__init__("Sensors")
        self.hub = hub

    def execute(self, quantity: str = "all", **_) -> ToolResult:
        order = ROOM_QUANTITIES if quantity == "all" else [quantity]
        order = [q for q in order if q in QUANTITIES and q not in HEALTH_QUANTITIES]
        if not order:
            return ToolResult(False, "I don't have a sensor for that.")

        snapshot = self.hub.snapshot(set(order))

        if not snapshot.readings:
            problems = " ".join(snapshot.problems) or "No room sensor is configured (SENSOR_SOURCES in settings)."
            return ToolResult(False, f"I have no sensor readings right now. {problems}", snapshot)

        if len(order) == 1 and snapshot.get(order[0]) is not None:
            reading = snapshot.get(order[0])
            place = f"your {reading.location}"
            if reading.quantity == "motion":
                text = (f"Motion detected in {place}" if reading.value else f"No motion in {place}")
                message = f"{text} ({reading.source}, {_age(reading)})."
            else:
                message = f"{reading.spec.label} in {place}: {reading.formatted()} ({reading.source}, {_age(reading)})."
            if order[0] in snapshot.stale:
                message += " That's the last known value; the sensor didn't answer just now."
            lines, simulated = [message], reading.simulated
        else:
            lines, simulated = _report(snapshot, order, "No sensor reported it.")
            lines = ["Room sensors:"] + [f"  {line}" for line in lines]

        if simulated:
            lines.append(SIMULATED_NOTE)
        lines += [f"Note: {problem}" for problem in snapshot.problems]
        return ToolResult(True, "\n".join(lines), snapshot)


class HealthTool(BaseTool):

    required_permissions = (Permission.SENSORS, Permission.HEALTH_DATA)

    def __init__(self, hub: SensorHub):
        super().__init__("Health")
        self.hub = hub

    def execute(self, quantity: str = "all", **_) -> ToolResult:
        if quantity == "sleep":
            return ToolResult(
                False,
                "I can't tell how you slept: no sleep tracker is connected. "
                "Wearable support is planned for a later milestone.",
            )

        order = HEALTH_ORDER if quantity == "all" else [quantity]
        order = [q for q in order if q in HEALTH_QUANTITIES]
        if not order:
            return ToolResult(False, "I don't have a health sensor for that.")

        snapshot = self.hub.snapshot(set(order), include_health=True)
        if not snapshot.readings:
            problems = " ".join(snapshot.problems)
            return ToolResult(
                False,
                "I have no reading. Place your fingertip gently on the sensor, keep still "
                "for about 10 seconds, and ask again." + (f" {problems}" if problems else ""),
                snapshot,
            )

        lines, simulated = _report(snapshot, order, "Keep your fingertip on the sensor and ask again.")
        if simulated:
            lines.append(SIMULATED_NOTE)
        lines += [f"Note: {problem}" for problem in snapshot.problems]
        lines.append(HEALTH_NOTE)
        return ToolResult(True, "\n".join(lines), snapshot)
