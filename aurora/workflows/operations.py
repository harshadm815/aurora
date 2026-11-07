"""
Follow-up operations on a value from an earlier step.

    "... and double it"              x 2
    "... then halve the result"      / 2
    "... and triple that"            x 3
    "... and square it"              x^2
    "... then add 5" / "plus 5"      + 5
    "... and subtract 3"             - 3
    "... and multiply it by 4"       x 4
    "... and divide it by 3"         / 3
    "... and round it"               nearest whole number
    "... and convert the temperature to Fahrenheit / Kelvin"

"it", "that", "the result" mean the previous step's main value; "the
temperature", "the humidity", "the average" pick that value by name.

split_trailing_operations() peels such clauses off the end of a message,
so the rest can be planned as usual: "what's the weather in Pune and
double the temperature" becomes the weather request plus one operation.
"""

import math
import re
from dataclasses import dataclass

from aurora.tool_plugins.calculator import format_number
from aurora.workflows.values import Value, check

_NUM = r"[-−]?\d+(?:\.\d+)?"
_REF = r"(?:it|that|this|the\s+(?P<label>result|answer|total|sum|average|mean|median|temperature|temp|humidity|wind(?:\s*speed)?|feels\s+like|percentage))"
_REF_ONLY = r"(?:it|that|this|the\s+(?:result|answer|total|sum|average|mean|median|temperature|temp|humidity|wind(?:\s*speed)?|feels\s+like|percentage))"

_JOIN = r"(?:^|,\s*|\s+)(?:and\s+(?:then\s+)?|then\s+|and\s+also\s+)?"

_PATTERNS = [
    ("multiply", 2, rf"double\s+{_REF}"),
    ("multiply", 3, rf"triple\s+{_REF}"),
    ("divide", 2, rf"(?:halve|half)\s+{_REF}"),
    ("square", None, rf"square\s+{_REF}"),
    ("add", "n", rf"add\s+(?P<n>{_NUM})(?:\s+to\s+{_REF})?"),
    # Bare "plus 5", "minus 3", "times 2" are deliberately not here: they are
    # ordinary arithmetic ("3 + 4 times 2") and belong to the calculator.
    ("add", "n", rf"increase\s+{_REF_ONLY}\s+by\s+(?P<n>{_NUM})(?!\s*%)"),
    ("subtract", "n", rf"(?:subtract|take\s+away)\s+(?P<n>{_NUM})(?:\s+from\s+{_REF})?"),
    ("subtract", "n", rf"(?:decrease|reduce)\s+{_REF_ONLY}\s+by\s+(?P<n>{_NUM})(?!\s*%)"),
    ("multiply", "n", rf"multiply\s+(?:{_REF}\s+)?by\s+(?P<n>{_NUM})"),
    ("divide", "n", rf"divide\s+(?:{_REF}\s+)?by\s+(?P<n>{_NUM})"),
    ("round", None, rf"round\s+{_REF}(?:\s+off)?"),
    ("convert", "unit", rf"(?:convert|change|turn)\s+{_REF}\s+(?:to|into)\s+(?P<unit>fahrenheit|kelvin|celsius)"),
    ("convert", "unit", rf"(?:what(?:'s|\s+is)\s+)?{_REF}\s+in\s+(?P<unit>fahrenheit|kelvin|celsius)"),
]
_COMPILED = [(kind, arg, re.compile(_JOIN + rf"(?P<op>{pattern})\s*[.?!]*\s*$", re.I)) for kind, arg, pattern in _PATTERNS]

UNIT_SYMBOLS = {"fahrenheit": "°F", "kelvin": "K", "celsius": "°C"}


class OperationError(ValueError):
    """The operation can't be applied to that value; the message says why."""


@dataclass(frozen=True)
class Operation:
    kind: str                        # multiply, divide, add, subtract, square, round, convert
    operand: float | int | None = None
    unit: str | None = None          # target unit for convert
    label: str | None = None         # which value: None = the previous step's main value
    text: str = ""                   # the user's words for this operation

    def describe(self) -> str:
        if self.kind == "convert":
            return f"convert to {self.unit}"
        if self.operand is None:
            return self.kind
        return f"{self.kind} {format_number(self.operand)}"


def _number(text: str):
    value = float(text.replace("−", "-"))
    return int(value) if value.is_integer() and "." not in text else value


def parse_operation(text: str) -> Operation | None:
    """An operation if the *whole* text is one ("double it", "and add 5")."""
    rest, found = split_trailing_operations(text)
    leftover = " ".join(rest.lower().replace(",", " ").split())
    if len(found) == 1 and leftover in ("", "and", "then", "also", "and then", "and also"):
        return found[0]
    return None


def _build(kind, arg, match) -> Operation:
    label = match.groupdict().get("label")
    label = " ".join(label.lower().split()) if label else None
    operand = None
    unit = None
    if arg == "n":
        operand = _number(match.group("n"))
    elif arg == "unit":
        unit = UNIT_SYMBOLS[match.group("unit").lower()]
    elif isinstance(arg, int):
        operand = arg
    return Operation(kind, operand, unit, label, match.group("op"))


def split_trailing_operations(message: str) -> tuple[str, list[Operation]]:
    """("what's the weather in Pune", [double]) from "... and double the
    temperature". Several can be chained: "... then double it and add 5"."""
    rest = message.strip()
    found: list[Operation] = []
    while True:
        for kind, arg, pattern in _COMPILED:
            match = pattern.search(rest)
            if match:
                found.insert(0, _build(kind, arg, match))
                rest = rest[:match.start()].rstrip(" ,")
                break
        else:
            break
    return rest, found


def apply(operation: Operation, value: Value) -> tuple[Value, str]:
    """The new value and one line saying exactly what was computed."""
    x, unit, name = value.number, value.unit, value.name
    shown = value.shown()
    kind, n = operation.kind, operation.operand

    if kind == "convert":
        if unit not in ("°C", "°F", "K"):
            raise OperationError(f"the {name} isn't a temperature, so it can't be converted to {operation.unit}")
        celsius = x if unit == "°C" else (x - 32) * 5 / 9 if unit == "°F" else x - 273.15
        target = operation.unit
        result = celsius if target == "°C" else celsius * 9 / 5 + 32 if target == "°F" else celsius + 273.15
        result = _tidy(result)
        new = Value(name, result, target)
        formula = {"°F": f"{format_number(celsius)} × 9/5 + 32", "K": f"{format_number(celsius)} + 273.15",
                   "°C": f"{shown}"}[target]
        return check(new), f"The {name} in {_unit_name(target)}: {formula} = {new.shown()}."

    if kind == "multiply":
        result, how = x * n, f"{shown} × {format_number(n)}"
    elif kind == "divide":
        if n == 0:
            raise OperationError("I can't divide by zero")
        result, how = x / n, f"{shown} ÷ {format_number(n)}"
    elif kind == "add":
        result, how = x + n, f"{shown} + {format_number(n)}{unit_suffix(unit)}"
    elif kind == "subtract":
        result, how = x - n, f"{shown} − {format_number(n)}{unit_suffix(unit)}"
    elif kind == "square":
        if unit:
            raise OperationError(f"squaring the {name} would change its unit, so I won't do that")
        result, how = x * x, f"{shown}²"
    elif kind == "round":
        result, how = round(x), f"{shown} rounded"
    else:
        raise OperationError(f"I don't know how to {kind} a value")

    # A doubled temperature is still a number in °C, but no longer a reading,
    # so it isn't held to the plausible-weather range.
    new = Value(name, _tidy(result), unit)
    verb = {"multiply": "Multiplied" if n not in (2, 3) else ("Doubled" if n == 2 else "Tripled"),
            "divide": "Halved" if n == 2 else "Divided", "add": "Added", "subtract": "Subtracted",
            "square": "Squared", "round": "Rounded"}[kind]
    return check(new), f"{verb}: {how} = {new.shown()}."


def unit_suffix(unit: str) -> str:
    return unit if unit in ("°C", "°F", "%") else (f" {unit}" if unit else "")


def _unit_name(unit: str) -> str:
    return {"°F": "Fahrenheit", "K": "kelvin", "°C": "Celsius"}[unit]


def _tidy(value):
    value = float(f"{float(value):.12g}")
    return int(value) if value.is_integer() and abs(value) < 1e15 else value
