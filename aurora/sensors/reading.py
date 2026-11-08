"""
What a sensor reading is, and which values are physically plausible.

Every reading is checked against a plausible range before AURORA uses
it: a BME280 reporting 150 °C is a wiring or sensor fault, not a fact
about the room.

Built-in quantities cover the room (temperature, humidity, pressure,
ambient light, motion, CO₂, VOCs, fine dust) and two informational
health readings. Other simple low-voltage sensors can be added in
``settings.SENSOR_EXTRA_QUANTITIES`` without changing code:

    SENSOR_EXTRA_QUANTITIES = [
        {"name": "soil_moisture", "label": "Soil moisture", "unit": "%", "minimum": 0, "maximum": 100},
    ]

They are shown and stored like the others but never interpreted (AURORA
doesn't know what a good value is) and can never be health data.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

from aurora.config import settings


@dataclass(frozen=True)
class Quantity:
    name: str
    label: str
    unit: str
    minimum: float | None = None
    maximum: float | None = None
    health: bool = False
    decimals: int = 1
    note: str = ""            # shown with the value, e.g. "estimated"
    custom: bool = False      # declared in settings, not built in

    @property
    def lower_label(self) -> str:
        """For mid-sentence use: 'ambient light', but 'CO₂' and 'VOCs' keep their capitals."""
        return self.label if self.label[:2].isupper() else self.label[:1].lower() + self.label[1:]


BUILT_IN = [
    Quantity("temperature", "Temperature", "°C", -40, 85),                        # BME280 range
    Quantity("humidity", "Humidity", "%", 0, 100, decimals=0),
    Quantity("pressure", "Air pressure", "hPa", 300, 1100, decimals=0),
    Quantity("light", "Ambient light", "lux", 0, 120_000, decimals=0),           # BH1750 / VEML7700
    Quantity("motion", "Motion", ""),
    Quantity("co2", "CO₂", "ppm", 250, 40_000, decimals=0),                      # NDIR / photoacoustic (SCD4x, MH-Z19)
    Quantity("eco2", "CO₂ (estimated)", "ppm", 400, 60_000, decimals=0,          # SGP30 / CCS811
             note="estimated from VOCs, not measured directly"),
    Quantity("tvoc", "VOCs", "ppb", 0, 60_000, decimals=0),
    Quantity("pm2_5", "Fine dust (PM2.5)", "µg/m³", 0, 1000, decimals=0),         # PMS5003 / SDS011
    Quantity("pm10", "Dust (PM10)", "µg/m³", 0, 2000, decimals=0),
    Quantity("heart_rate", "Heart rate", "bpm", 30, 220, health=True, decimals=0),
    Quantity("spo2", "Blood oxygen (SpO2)", "%", 70, 100, health=True, decimals=0),
]

QUANTITIES: dict[str, Quantity] = {q.name: q for q in BUILT_IN}
HEALTH_QUANTITIES = {name for name, q in QUANTITIES.items() if q.health}
ROOM_QUANTITIES = [q.name for q in BUILT_IN if not q.health]

_NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


def register_extra(entries: list[dict] | None = None) -> list[str]:
    """Adds the quantities from settings.SENSOR_EXTRA_QUANTITIES. Returns problems
    (bad entries are skipped, never half-added). Built-in names can't be replaced."""
    problems = []
    for entry in settings.SENSOR_EXTRA_QUANTITIES if entries is None else entries:
        name = entry.get("name", "") if isinstance(entry, dict) else ""
        try:
            if not _NAME.match(name):
                raise ValueError("needs a lower-case name like 'soil_moisture'")
            if name in QUANTITIES and not QUANTITIES[name].custom:
                raise ValueError("is a built-in quantity")
            minimum, maximum = entry.get("minimum"), entry.get("maximum")
            for bound in (minimum, maximum):
                if bound is not None and (isinstance(bound, bool) or not isinstance(bound, (int, float))):
                    raise ValueError("minimum and maximum must be numbers")
            if minimum is not None and maximum is not None and minimum >= maximum:
                raise ValueError("minimum must be below maximum")
            quantity = Quantity(name, str(entry.get("label") or name.replace("_", " ").capitalize())[:40],
                                str(entry.get("unit", ""))[:12], minimum, maximum,
                                decimals=int(entry.get("decimals", 1)), custom=True)
        except (ValueError, TypeError) as error:
            problems.append(f"Extra sensor quantity {name or entry!r} skipped: {error}.")
            continue
        QUANTITIES[name] = quantity
        if name not in ROOM_QUANTITIES:
            ROOM_QUANTITIES.append(name)
    return problems


@dataclass(frozen=True)
class Reading:
    quantity: str
    value: float | bool
    source: str
    location: str = "room"
    simulated: bool = False
    time: datetime = field(default_factory=datetime.now)    # when it was measured

    @property
    def spec(self) -> Quantity:
        return QUANTITIES[self.quantity]

    @property
    def is_health(self) -> bool:
        return self.quantity in HEALTH_QUANTITIES

    def formatted(self) -> str:
        if self.quantity == "motion":
            return "motion detected" if self.value else "no motion"
        spec = self.spec
        number = f"{self.value:,.{spec.decimals}f}" if abs(self.value) >= 10_000 else f"{self.value:.{spec.decimals}f}"
        if not spec.unit:
            return number
        return f"{number}%" if spec.unit == "%" else f"{number} {spec.unit}"


def validate(quantity: str, value) -> tuple[bool, str]:
    """(ok, reason). Rejects unknown quantities, wrong types and implausible values."""
    spec = QUANTITIES.get(quantity)
    if spec is None:
        return False, "unknown quantity"
    if quantity == "motion":
        return (isinstance(value, bool), "ok" if isinstance(value, bool) else "motion must be true or false")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, "not a number"
    if value != value or value in (float("inf"), float("-inf")):   # NaN/inf: a disconnected sensor
        return False, "not a number"
    if spec.minimum is not None and value < spec.minimum:
        return False, f"below the plausible range ({spec.minimum:g} {spec.unit})".replace(" )", ")")
    if spec.maximum is not None and value > spec.maximum:
        return False, f"above the plausible range ({spec.maximum:g} {spec.unit})".replace(" )", ")")
    return True, "ok"
