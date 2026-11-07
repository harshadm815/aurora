"""
Response validation: checks on what a workflow is about to say.

AURORA composes some text itself (calculation explanations, follow-up
operations). Before it is shown, every number in that text must be one
the workflow can account for: a number from the user's message, a value
a tool produced, or a value the workflow computed. A number that can't
be accounted for means something is wrong (a bug in a template, a
mismatched value), so the text is replaced by a plain statement of the
result rather than shown.

Text written by tools that report outside data (the weather report) is
not checked this way; their values are checked for plausibility instead
(see values.py).
"""

import re

from aurora.tool_plugins.calculator import format_number

_NUMBER = re.compile(r"(?<![\w.])[-−]?\d+(?:\.\d+)?(?![\w])")

# Constants that explanations legitimately contain (unit conversion, percentages, halves).
CONSTANTS = {"2", "3", "5", "9", "32", "100", "273.15"}


def numbers_in(text: str) -> set[str]:
    return {format_number(_to_number(m.group())) for m in _NUMBER.finditer(text.replace("−", "-"))}


def _to_number(text: str):
    value = float(text.replace("−", "-"))
    return int(value) if value.is_integer() else value


def as_strings(numbers) -> set[str]:
    result = set()
    for n in numbers:
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            continue
        result.add(format_number(n))
        result.add(format_number(abs(n)))      # "−3" is read as a sign plus 3
    return result


def unexplained_numbers(text: str, allowed) -> set[str]:
    """Numbers in `text` that are neither allowed nor standard constants."""
    allowed = as_strings(allowed) | CONSTANTS
    return {n for n in numbers_in(text) if n.lstrip("-") not in allowed and n not in allowed}


def numbers_tool_allowed(data: dict) -> list:
    """Every number a Numbers result may legitimately mention."""
    allowed = list(data.get("numbers") or [])
    allowed += [v for v in (data.get("values") or {}).values() if v is not None]
    for details in (data.get("details") or {}).values():
        for value in details.values():
            if isinstance(value, list):
                allowed += value
            else:
                allowed.append(value)
        if "count" in details:
            allowed += [details["count"], details["count"] - 1]   # sample standard deviation divides by n − 1
    return allowed
