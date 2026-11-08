"""
What sensor readings mean, from fixed reference bands and stored history.

Interpretations only ever describe a reading AURORA actually has: no
reading, no interpretation. Each band says where its numbers come from,
and a simulated reading is interpreted with a reminder that it isn't a
measurement of the real room.

    interpret(reading)          -> Interpretation (level, summary, advice, reference) or None
    trend(history, quantity)    -> Trend ("rising: up 1.4 °C in the last 52 minutes") or None
    day_summary(history)        -> lowest / highest / average with times
    window_advice(snapshot)     -> whether opening a window would help, and why

Health readings (heart rate, SpO2) are never interpreted here: they are
informational, not diagnostic.

These are general indoor guidelines for comfort and air quality, not
safety limits. People differ, and so do sensors: the cheap ones used for
learning drift and need calibration.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from aurora.sensors.reading import QUANTITIES, Reading

GOOD, FAIR, POOR, INFO = "good", "fair", "poor", "info"


@dataclass(frozen=True)
class Band:
    below: float              # this band applies to values below this
    level: str
    summary: str
    advice: str = ""


# Each quantity: (bands in rising order, reference shown with the interpretation).
BANDS: dict[str, tuple[list[Band], str]] = {
    "temperature": ([
        Band(16, POOR, "cold", "Warm the room or put on another layer."),
        Band(20, FAIR, "cool", ""),
        Band(26, GOOD, "comfortable", ""),
        Band(30, FAIR, "warm", "A fan or some fresh air may help."),
        Band(float("inf"), POOR, "hot", "Drink water and cool the room if you can."),
    ], "20–26 °C is a typical indoor comfort range; it depends on clothing and activity."),
    "humidity": ([
        Band(20, POOR, "very dry", "Very dry air can irritate eyes and throat."),
        Band(30, FAIR, "dry", ""),
        Band(60, GOOD, "comfortable", ""),
        Band(70, FAIR, "humid", "Airing the room would help."),
        Band(float("inf"), POOR, "very humid", "Mould can grow if it stays this humid; air the room."),
    ], "30–60 % is the indoor range commonly recommended (e.g. by the US EPA) to limit mould and dryness."),
    "co2": ([
        Band(1000, GOOD, "fresh enough", ""),
        Band(1500, FAIR, "getting stuffy", "Opening a window would help."),
        Band(float("inf"), POOR, "stuffy", "Open a window: the air needs changing."),
    ], "Outdoor air is about 420 ppm; 1000 ppm is a common guideline for good ventilation."),
    "eco2": ([
        Band(1000, GOOD, "fresh enough", ""),
        Band(1500, FAIR, "getting stuffy", "Opening a window would help."),
        Band(float("inf"), POOR, "stuffy", "Open a window: the air needs changing."),
    ], "Estimated from VOCs, not measured directly, so treat it as a rough guide. "
       "1000 ppm is a common guideline for good ventilation."),
    "tvoc": ([
        Band(220, GOOD, "low", ""),
        Band(660, FAIR, "moderate", "Airing the room would help."),
        Band(2200, POOR, "high", "Air the room; look for a source (paint, cleaning products, cooking)."),
        Band(float("inf"), POOR, "very high", "Air the room well; look for a source."),
    ], "Bands commonly used for indoor total VOCs (220 / 660 / 2200 ppb)."),
    "pm2_5": ([
        Band(15.001, GOOD, "low", ""),
        Band(35.001, FAIR, "above the WHO guideline", "Avoid smoke and burning; an air purifier helps."),
        Band(55.001, POOR, "unhealthy for sensitive people", "Keep windows closed if the air outside is bad; "
             "an air purifier helps."),
        Band(float("inf"), POOR, "unhealthy", "Reduce sources (smoke, frying, incense); an air purifier helps."),
    ], "WHO 2021 guideline: 15 µg/m³ as a 24-hour average (a single reading isn't an average)."),
    "pm10": ([
        Band(45.001, GOOD, "low", ""),
        Band(100, FAIR, "raised", "Dust or smoke nearby? Airing or cleaning may help."),
        Band(float("inf"), POOR, "high", "Reduce dust and smoke sources."),
    ], "WHO 2021 guideline: 45 µg/m³ as a 24-hour average."),
    "light": ([
        Band(10, INFO, "dark", ""),
        Band(100, INFO, "dim", "Too dim for reading."),
        Band(300, INFO, "fine for moving around, dim for reading", "Turn on a light to read or work."),
        Band(1000, INFO, "good for reading and desk work", ""),
        Band(float("inf"), INFO, "very bright (like daylight)", ""),
    ], "Desks are usually lit to about 300–500 lux (e.g. EN 12464-1)."),
}

# How much change counts as "rising" or "falling", and over what window.
TREND = {
    "temperature": (0.5, timedelta(hours=1)),
    "humidity": (3, timedelta(hours=1)),
    "pressure": (1.0, timedelta(hours=3)),
    "co2": (150, timedelta(hours=1)),
    "eco2": (150, timedelta(hours=1)),
    "tvoc": (100, timedelta(hours=1)),
    "pm2_5": (5, timedelta(hours=1)),
    "pm10": (10, timedelta(hours=1)),
}

SIMULATED_CAVEAT = "This is a simulated value, so it says nothing about your real room."


@dataclass(frozen=True)
class Interpretation:
    quantity: str
    level: str          # good / fair / poor / info
    summary: str        # "comfortable"
    advice: str
    reference: str
    simulated: bool = False

    def sentence(self) -> str:
        text = f"{QUANTITIES[self.quantity].label}: {self.summary}."
        if self.advice:
            text += f" {self.advice}"
        if self.simulated:
            text += f" ({SIMULATED_CAVEAT})"
        return text


def interpret(reading: Reading | None) -> Interpretation | None:
    """The band a reading falls in, or None (no reading, motion, health or custom quantities)."""
    if reading is None or reading.quantity not in BANDS or reading.is_health:
        return None
    bands, reference = BANDS[reading.quantity]
    for band in bands:
        if reading.value < band.below:
            return Interpretation(reading.quantity, band.level, band.summary, band.advice, reference,
                                  reading.simulated)
    return None


@dataclass(frozen=True)
class Trend:
    quantity: str
    direction: str            # rising / falling / steady
    change: float
    minutes: int
    simulated: bool

    def sentence(self) -> str:
        spec = QUANTITIES[self.quantity]
        unit = "%" if spec.unit == "%" else f" {spec.unit}"
        span = f"{self.minutes} minutes" if self.minutes < 90 else f"{self.minutes / 60:.0f} hours"
        if self.direction == "steady":
            text = f"{spec.label} has been steady over the last {span}"
        else:
            word = "up" if self.change > 0 else "down"
            text = f"{spec.label} is {self.direction}: {word} {abs(self.change):.{spec.decimals}f}{unit} in the last {span}"
        return text + (" (simulated)." if self.simulated else ".")


def trend(history: list[Reading], quantity: str, now: datetime | None = None) -> Trend | None:
    """Direction of change over the quantity's window, from stored readings.

    Needs at least 3 readings spanning a quarter of the window, all of the
    same kind (real or simulated) as the newest one; otherwise None."""
    if quantity not in TREND or not history:
        return None
    threshold, window = TREND[quantity]
    now = now or datetime.now()
    latest = max(history, key=lambda r: r.time)
    points = [r for r in history if r.time >= now - window and r.simulated == latest.simulated
              and r.source == latest.source]
    points.sort(key=lambda r: r.time)
    if len(points) < 3 or points[-1].time - points[0].time < window / 4:
        return None

    # Least-squares slope (per second) times the span: robust to one noisy reading.
    times = [(r.time - points[0].time).total_seconds() for r in points]
    values = [float(r.value) for r in points]
    mean_t, mean_v = sum(times) / len(times), sum(values) / len(values)
    spread = sum((t - mean_t) ** 2 for t in times)
    slope = sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values)) / spread if spread else 0.0
    change = slope * times[-1]
    minutes = round(times[-1] / 60)
    if abs(change) < threshold:
        return Trend(quantity, "steady", change, minutes, latest.simulated)
    return Trend(quantity, "rising" if change > 0 else "falling", change, minutes, latest.simulated)


@dataclass(frozen=True)
class DaySummary:
    quantity: str
    lowest: Reading
    highest: Reading
    average: float
    count: int
    simulated: bool

    def sentence(self) -> str:
        spec = QUANTITIES[self.quantity]
        unit = "%" if spec.unit == "%" else f" {spec.unit}"
        text = (f"{spec.label} over the last 24 hours ({self.count} readings): lowest "
                f"{self.lowest.formatted()} at {self.lowest.time:%H:%M}, highest {self.highest.formatted()} "
                f"at {self.highest.time:%H:%M}, average {self.average:.{spec.decimals}f}{unit}")
        return text + (" (simulated)." if self.simulated else ".")


def day_summary(history: list[Reading]) -> DaySummary | None:
    numbers = [r for r in history if r.quantity != "motion"]
    if len(numbers) < 2:
        return None
    lowest = min(numbers, key=lambda r: r.value)
    highest = max(numbers, key=lambda r: r.value)
    average = sum(float(r.value) for r in numbers) / len(numbers)
    return DaySummary(numbers[0].quantity, lowest, highest, average, len(numbers),
                      any(r.simulated for r in numbers))


AIR = ("co2", "eco2", "tvoc", "pm2_5", "pm10")


def window_advice(readings: dict[str, Reading]) -> tuple[str, list[str]]:
    """('yes' / 'no' / 'unknown', reasons) from the readings AURORA has.

    Only stale air (CO₂, VOCs) and high humidity are reasons to open a
    window; dust is not (outdoor air may be dustier). Without an air or
    humidity sensor the answer is 'unknown'."""
    reasons, checked = [], []
    for quantity in ("co2", "eco2", "tvoc", "humidity"):
        meaning = interpret(readings.get(quantity))
        if meaning is None:
            continue
        checked.append(quantity)
        if meaning.level in (FAIR, POOR) and quantity != "humidity":
            reasons.append(f"{QUANTITIES[quantity].label} is {readings[quantity].formatted()} ({meaning.summary})")
        elif quantity == "humidity" and readings[quantity].value >= 60:
            reasons.append(f"humidity is {readings[quantity].formatted()} ({meaning.summary})")
    if reasons:
        return "yes", reasons
    if any(q in checked for q in ("co2", "eco2", "tvoc")):
        return "no", [f"{QUANTITIES[q].label} is {readings[q].formatted()}" for q in checked]
    return "unknown", [f"{QUANTITIES[q].label} is {readings[q].formatted()}" for q in checked]
