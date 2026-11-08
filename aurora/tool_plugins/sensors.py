"""
Sensor tools.

- Sensors: room readings (temperature, humidity, air pressure, ambient
  light, motion, CO₂, VOCs, fine dust, and any extra quantities declared
  in settings), what they mean, how they have changed, and whether
  opening a window would help.
- Health:  heart rate and blood oxygen from a fingertip sensor. A separate
  tool with its own ``health_data`` permission, so it can be switched off
  on its own. Readings are informational, never medical advice, and are
  never interpreted.

Every value says where it came from and when it was measured. Simulated
values are always labelled as simulated. With no reading there is no
answer, only an explanation of what's missing.
"""

from datetime import datetime, timedelta

from aurora.kernel.permissions import Permission
from aurora.sensors.hub import SensorHub, Snapshot
from aurora.sensors.interpret import AIR, FAIR, GOOD, POOR, day_summary, interpret, trend, window_advice
from aurora.sensors.reading import HEALTH_QUANTITIES, QUANTITIES, ROOM_QUANTITIES, Reading
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

HEALTH_ORDER = ["heart_rate", "spo2"]
# Shown as "no reading" in the overview when nothing reports them (the
# secondary ones, PM10 and estimated CO₂, only appear when present).
PRIMARY = ["temperature", "humidity", "pressure", "light", "motion", "co2", "tvoc", "pm2_5"]
COMFORT = ["temperature", "humidity", "light", *AIR]

SIMULATED_NOTE = "(Simulated values: no real sensor is connected yet.)"
HEALTH_NOTE = (
    "This is informational only, not a medical measurement. "
    "If you feel unwell, please contact a doctor."
)
NO_AIR_SENSOR = ("No air-quality sensor is connected, so I can't tell how the air is. "
                 "Supported: CO₂ (e.g. SCD40, MH-Z19), VOCs and estimated CO₂ (SGP30), "
                 "fine dust (PMS5003); see firmware/esp32_sensor_hub.")


def _age(reading: Reading, now: datetime | None = None) -> str:
    seconds = ((now or datetime.now()) - reading.time).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 120:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    return f"{minutes // 60} hours ago"


def _when(reading: Reading, stale: bool = False) -> str:
    """'Room ESP32, 10:42, just now' - where and when it was measured."""
    text = f"{reading.source}, {reading.time:%H:%M}, {_age(reading)}"
    return text + (", last known value" if stale else "")


def _line(reading: Reading, stale: bool) -> str:
    line = f"{reading.spec.label}: {reading.formatted()} ({_when(reading, stale)})"
    meaning = interpret(reading)
    if meaning is not None:
        line += f": {meaning.summary}"
    return line


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


def _labels(quantities) -> str:
    names = [QUANTITIES[q].lower_label for q in quantities]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


class SensorsTool(BaseTool):

    required_permissions = (Permission.SENSORS,)

    def __init__(self, hub: SensorHub):
        super().__init__("Sensors")
        self.hub = hub

    def execute(self, quantity: str = "all", view: str = "now", **_) -> ToolResult:
        if view == "trend":
            return self.history(quantity)
        if view == "last_motion":
            return self.last_motion()
        if quantity in ("all", "air", "comfort", "window"):
            return self.overview(quantity)

        if quantity not in QUANTITIES or quantity in HEALTH_QUANTITIES:
            return ToolResult(False, "I don't have a sensor for that.")
        wanted = {quantity, "eco2"} if quantity == "co2" else {quantity}   # either kind of CO₂ sensor
        snapshot = self.hub.snapshot(wanted)
        reading = snapshot.get(quantity) or (snapshot.get("eco2") if quantity == "co2" else None)
        if reading is None:
            problems = " ".join(snapshot.problems)
            if snapshot.readings or not problems:
                message = f"I have no {QUANTITIES[quantity].lower_label} reading: no connected sensor reports it."
                if quantity in AIR:
                    message = NO_AIR_SENSOR
            else:
                message = f"I have no sensor readings right now. {problems}"
            return ToolResult(False, message.strip(), snapshot)

        stale = reading.quantity in snapshot.stale
        place = f"your {reading.location}"
        if reading.quantity == "motion":
            text = f"Motion detected in {place}" if reading.value else f"No motion in {place}"
            lines = [f"{text} ({_when(reading)})."]
        else:
            lines = [f"{reading.spec.label} in {place}: {reading.formatted()} ({_when(reading)})."]
            meaning = interpret(reading)
            if meaning is not None:
                lines[0] = lines[0][:-1] + f": {meaning.summary}." + (f" {meaning.advice}" if meaning.advice else "")
                lines.append(f"Guide: {meaning.reference}")
            change = trend(self.hub.store.history(reading.quantity, datetime.now() - timedelta(hours=3)),
                           reading.quantity)
            if change is not None and not stale:
                lines.append(change.sentence())
        if stale:
            lines[0] += " That's the last known value; the sensor didn't answer just now."
        if reading.simulated:
            lines.append(SIMULATED_NOTE)
        lines += [f"Note: {problem}" for problem in snapshot.problems]
        return ToolResult(True, "\n".join(lines), snapshot)

    # --- Several readings ------------------------------------------------------------------

    def overview(self, kind: str) -> ToolResult:
        wanted = {"all": ROOM_QUANTITIES, "air": AIR, "comfort": COMFORT, "window": ["humidity", *AIR]}[kind]
        snapshot = self.hub.snapshot(set(wanted))
        if kind == "air" and not snapshot.readings and not snapshot.problems:
            return ToolResult(False, NO_AIR_SENSOR, snapshot)
        if not snapshot.readings:
            problems = " ".join(snapshot.problems) or "No room sensor is configured (SENSOR_SOURCES in settings)."
            return ToolResult(False, f"I have no sensor readings right now. {problems}", snapshot)

        simulated = any(r.simulated for r in snapshot.readings.values())
        if kind == "window":
            lines = self._window(snapshot)
        else:
            present = [q for q in wanted if snapshot.get(q) is not None]
            if kind == "air" and not any(q in present for q in AIR):
                return ToolResult(False, NO_AIR_SENSOR + self._notes(snapshot), snapshot)
            heading = {"all": "Room sensors:", "air": "Air in your room:", "comfort": "Your room:"}[kind]
            lines = [heading] + [f"  {_line(snapshot.get(q), q in snapshot.stale)}" for q in present]
            if kind in ("air", "comfort"):
                lines.append(self._verdict(snapshot, present))
            if kind == "all":
                missing = [q for q in PRIMARY if q not in present and not (q == "co2" and "eco2" in present)]
                missing += [q for q in ROOM_QUANTITIES if QUANTITIES[q].custom and q not in present]
                if missing:
                    lines.append(f"No reading for: {_labels(missing)}.")
        if simulated:
            lines.append(SIMULATED_NOTE)
        lines += [f"Note: {problem}" for problem in snapshot.problems]
        return ToolResult(True, "\n".join(lines), snapshot)

    @staticmethod
    def _notes(snapshot) -> str:
        return "".join(f"\nNote: {problem}" for problem in snapshot.problems)

    @staticmethod
    def _verdict(snapshot: Snapshot, present: list[str]) -> str:
        meanings = [m for m in (interpret(snapshot.get(q)) for q in present if q != "light") if m is not None]
        poor = [m for m in meanings if m.level == POOR]
        fair = [m for m in meanings if m.level == FAIR]
        if not meanings:
            return "I can't judge these readings."
        if poor:
            advice = next((m.advice for m in poor if m.advice), "")
            return f"Needs attention: {_labels([m.quantity for m in poor])}. {advice}".strip()
        if fair:
            advice = next((m.advice for m in fair if m.advice), "")
            return f"Mostly fine; {_labels([m.quantity for m in fair])} could be better. {advice}".strip()
        checked = _labels([m.quantity for m in meanings if m.level == GOOD])
        return f"All good ({checked})."

    @staticmethod
    def _window(snapshot: Snapshot) -> list[str]:
        readings = {q: r for q, r in snapshot.readings.items() if q not in snapshot.stale}
        answer, reasons = window_advice(readings)
        if answer == "yes":
            return [f"Yes, opening a window would help: {'; '.join(reasons)}."]
        if answer == "no":
            return [f"There's no need from the readings I have: {'; '.join(reasons)}. "
                    "Open one anyway if it feels stuffy or too warm."]
        known = f" I only know that {'; '.join(reasons).lower()}." if reasons else ""
        return [f"I can't tell: no CO₂ or VOC sensor is connected.{known}"]

    # --- History ---------------------------------------------------------------------------

    def history(self, quantity: str) -> ToolResult:
        if quantity not in QUANTITIES or quantity in HEALTH_QUANTITIES or quantity == "motion":
            return ToolResult(False, "I can only show how room readings like temperature or humidity changed.")
        since = datetime.now() - timedelta(hours=24)
        readings = self.hub.store.history(quantity, since)
        if quantity == "co2" and not readings:
            readings = self.hub.store.history("eco2", since)
        label = QUANTITIES[quantity].lower_label
        if len(readings) < 2:
            return ToolResult(False, f"I don't have enough stored {label} readings from the last 24 hours to "
                                     "show how it changed. Turn on background watching in the Sensor Centre "
                                     "to collect them.", readings)
        lines = []
        summary = day_summary(readings)
        if summary is not None:
            lines.append(summary.sentence())
        change = trend(readings, readings[-1].quantity)
        if change is not None:
            lines.append(change.sentence())
        if any(r.simulated for r in readings):
            lines.append(SIMULATED_NOTE)
        return ToolResult(True, "\n".join(lines), readings)

    def last_motion(self) -> ToolResult:
        seen = self.hub.store.last_motion()
        if seen is None:
            return ToolResult(True, "I haven't seen any motion in the stored readings (the last "
                                    "30 days). Motion is only recorded when sensors are read.", None)
        day = "today" if seen.time.date() == datetime.now().date() else f"on {seen.time:%d %b}"
        message = f"Motion was last seen {day} at {seen.time:%H:%M} ({_age(seen)}, {seen.source})."
        if seen.simulated:
            message += f" {SIMULATED_NOTE}"
        return ToolResult(True, message, seen)


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
