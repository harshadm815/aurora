"""
What a sensor reading is, and which values are physically plausible.

Every reading is checked against a plausible range before AURORA uses
it: a BME280 reporting 150 °C is a wiring or sensor fault, not a fact
about the room.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Quantity:
    name: str
    label: str
    unit: str
    minimum: float | None = None
    maximum: float | None = None
    health: bool = False
    decimals: int = 1


QUANTITIES = {
    q.name: q for q in [
        Quantity("temperature", "Temperature", "°C", -40, 85),       # BME280 range
        Quantity("humidity", "Humidity", "%", 0, 100, decimals=0),
        Quantity("pressure", "Air pressure", "hPa", 300, 1100, decimals=0),
        Quantity("motion", "Motion", ""),
        Quantity("heart_rate", "Heart rate", "bpm", 30, 220, health=True, decimals=0),
        Quantity("spo2", "Blood oxygen (SpO2)", "%", 70, 100, health=True, decimals=0),
    ]
}

HEALTH_QUANTITIES = {name for name, q in QUANTITIES.items() if q.health}


@dataclass(frozen=True)
class Reading:
    quantity: str
    value: float | bool
    source: str
    location: str = "room"
    simulated: bool = False
    time: datetime = field(default_factory=datetime.now)

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
        number = f"{self.value:.{spec.decimals}f}"
        return f"{number} {spec.unit}" if spec.unit != "%" else f"{number}%"


def validate(quantity: str, value) -> tuple[bool, str]:
    """(ok, reason). Rejects unknown quantities, wrong types and implausible values."""
    spec = QUANTITIES.get(quantity)
    if spec is None:
        return False, "unknown quantity"
    if quantity == "motion":
        return (isinstance(value, bool), "ok" if isinstance(value, bool) else "motion must be true or false")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, "not a number"
    if value != value:   # NaN, e.g. a disconnected sensor
        return False, "not a number"
    if spec.minimum is not None and value < spec.minimum:
        return False, f"below the plausible range ({spec.minimum} {spec.unit})"
    if spec.maximum is not None and value > spec.maximum:
        return False, f"above the plausible range ({spec.maximum} {spec.unit})"
    return True, "ok"
