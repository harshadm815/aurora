"""
Typed values that one workflow step can hand to the next.

Tools reply with a message for the user and structured `data`. This
module turns the data of the tools that produce numbers into named
Values with units, and checks they are plausible before anything uses
them:

    Calculator  result
    Numbers     result (and one value per calculation: average, median, ...)
    Weather     temperature, feels like (°C), humidity (%), wind (km/h)
                for "now" only, rounded exactly as the user was shown

A value that fails its plausibility check (a temperature of 900 °C, a
humidity of 140 %) is never used: the step that produced it counts as
failed.
"""

import math
from dataclasses import dataclass

from aurora.tool_plugins.calculator import format_number

MAX_MAGNITUDE = 1e15

# Plausible ranges for measured quantities, by unit.
PLAUSIBLE = {
    "°C": (-90.0, 60.0),        # outdoor air temperature on Earth
    "%": (0.0, 100.0),
    "km/h": (0.0, 500.0),
}


class ImplausibleValue(ValueError):
    """A tool produced a value that can't be right."""


@dataclass(frozen=True)
class Value:
    name: str                  # "temperature", "result", "average", ...
    number: float | int
    unit: str = ""             # "°C", "%", "km/h" or "" for a plain number
    measured: bool = False     # True for readings from the world (checked against PLAUSIBLE)

    def shown(self) -> str:
        unit = self.unit
        return f"{format_number(self.number)}{unit if unit in ('°C', '°F', '%') else (' ' + unit if unit else '')}"


def check(value: Value) -> Value:
    number = value.number
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
        raise ImplausibleValue(f"the {value.name} isn't a valid number")
    if abs(number) > MAX_MAGNITUDE:
        raise ImplausibleValue(f"the {value.name} is too large to work with")
    limits = PLAUSIBLE.get(value.unit) if value.measured else None
    if limits and not limits[0] <= number <= limits[1]:
        raise ImplausibleValue(f"a {value.name} of {value.shown()} can't be right")
    return value


NUMBERS_LABELS = {
    "mean": "average", "median": "median", "mode": "mode", "sum": "sum", "count": "count",
    "min": "minimum", "max": "maximum", "range": "range", "stdev": "standard deviation",
    "percent_of": "result", "what_percent": "percentage", "increase": "result", "decrease": "result",
}


def values_from(tool: str, data) -> list[Value]:
    """The values a tool result provides, primary value first. Raises
    ImplausibleValue if any of them fails its check."""
    if not isinstance(data, (dict, int, float)) or isinstance(data, bool):
        return []
    found: list[Value] = []
    if tool == "Calculator" and isinstance(data, (int, float)):
        found.append(Value("result", data))
    elif tool == "Numbers" and isinstance(data, dict):
        primary = data.get("value")
        if primary is not None:
            found.append(Value("result", primary, "%" if data.get("operation") == "what_percent" else ""))
        for operation, number in (data.get("values") or {}).items():
            if number is not None:
                found.append(Value(NUMBERS_LABELS.get(operation, operation), number,
                                   "%" if operation == "what_percent" else ""))
    elif tool == "Weather" and isinstance(data, dict) and data.get("when", "now") == "now":
        current = (data.get("forecast") or {}).get("current") or {}
        for name, key, unit in (("temperature", "temperature_2m", "°C"),
                                ("feels like", "apparent_temperature", "°C"),
                                ("humidity", "relative_humidity_2m", "%"),
                                ("wind", "wind_speed_10m", "km/h")):
            if key in current:
                # Rounded the way the weather reply showed it, so later steps
                # use the number the user actually saw.
                number = current[key]
                number = round(number) if isinstance(number, (int, float)) and not isinstance(number, bool) else number
                found.append(Value(name, number, unit, measured=True))
    return [check(v) for v in found]


def pick(values: list[Value], name: str | None) -> Value | None:
    """The value called `name` ("temperature"), or the primary value."""
    if not values:
        return None
    if name is None:
        return values[0]
    name = name.lower().strip()
    aliases = {"temp": "temperature", "mean": "average", "answer": "result", "total": "sum",
               "wind speed": "wind", "windspeed": "wind"}
    name = aliases.get(name, name)
    for value in values:
        if value.name == name:
            return value
    return None
