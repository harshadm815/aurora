"""
Routing rule for number questions: statistics and percentages.

    "what is the average of 4, 8 and 15"
    "Find the average of these numbers and explain the result: 4, 8, 15, 16, 23, 42"
    "mean and median of 3 1 9 4"
    "calculate 15% of 80", "what percent is 12 of 80", "increase 80 by 15%"

It only fires when the message names a calculation *and* contains the
numbers (at least two), so "the average temperature in Pune" or "the
total of my bills" are left to other rules.

Where the numbers are read from: after a colon if there is one,
otherwise after the calculation word. "these 6 numbers" is read as a
stated count (checked against what was found), not as data.

Commas: "4, 8, 15" is a list; "1,000" is one thousand. If any comma in
the list is followed by a space, or by something other than exactly
three digits, every comma separates numbers; otherwise commas between
digit groups are thousands separators.
"""

import re

from aurora.routing import decision as targets
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent

_OPERATIONS = [
    ("mean", r"average|mean|arithmetic\s+mean"),
    ("median", r"median|middle\s+value"),
    ("mode", r"mode|most\s+common\s+(?:number|value)"),
    ("stdev", r"standard\s+deviation|std\.?\s*dev(?:iation)?"),
    ("sum", r"sum|total|add\s+up"),
    ("count", r"how\s+many\s+numbers|count\s+of"),
    ("range", r"range"),
    ("min", r"minimum|min|smallest|lowest"),
    ("max", r"maximum|max|largest|biggest|highest"),
]
_OPERATION = re.compile(r"\b(?:" + "|".join(f"(?P<{name}>{pattern})" for name, pattern in _OPERATIONS) + r")\b", re.I)

_EXPLAIN = re.compile(
    r"\b(?:explain|explanation|why|show\s+(?:me\s+)?(?:the|your)\s+(?:working|work|steps)|"
    r"step\s+by\s+step|walk\s+me\s+through|how\s+(?:did|do)\s+you)\b", re.I)

_STATED_COUNT = re.compile(r"\b(?:these|those|the\s+following|following)\s+(\d+)\s+(?:numbers|values)\b", re.I)

_NUMBER_TEXT = r"[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[-−]?\.\d+"
_NUMBER = re.compile(rf"(?<![\w.])(?:{_NUMBER_TEXT})(?![\w])")
_LIST_COMMAS = re.compile(r",\s|,\d{1,2}(?!\d)|,\d{4,}|,[-−.]")



def _P(name: str) -> str:
    return f"(?P<{name}>{_NUMBER_TEXT})"


_PERCENT_OF = re.compile(_P("a") + r"\s*(?:%|percent|per\s+cent)\s+of\s+" + _P("b"), re.I)
_WHAT_PERCENT = re.compile(
    r"what\s+(?:percent(?:age)?|%)\s+(?:is\s+" + _P("a") + r"\s+of\s+" + _P("b")
    + r"|of\s+" + _P("b2") + r"\s+is\s+" + _P("a2") + r")", re.I)
_CHANGE = re.compile(
    r"\b(?P<verb>increase|raise|add|decrease|reduce|cut|lower|subtract)\s+" + _P("a")
    + r"\s+by\s+" + _P("b") + r"\s*(?:%|percent|per\s+cent)", re.I)


def parse_number(text: str):
    text = text.replace("−", "-").replace(",", "")
    value = float(text)
    return int(value) if value.is_integer() and "." not in text else value


def extract_numbers(region: str) -> list:
    """Numbers in a piece of text, using the comma rule described above."""
    if _LIST_COMMAS.search(region):
        region = region.replace(",", " , ")
    return [parse_number(m.group()) for m in _NUMBER.finditer(region)]


def parse_percentage(text: str) -> dict | None:
    match = _WHAT_PERCENT.search(text)
    if match:
        a = match.group("a") or match.group("a2")
        b = match.group("b") or match.group("b2")
        return {"operation": "what_percent", "numbers": (parse_number(a), parse_number(b))}
    match = _CHANGE.search(text)
    if match:
        verb = match.group("verb").lower()
        operation = "increase" if verb in ("increase", "raise", "add") else "decrease"
        return {"operation": operation, "numbers": (parse_number(match.group("a")), parse_number(match.group("b")))}
    match = _PERCENT_OF.search(text)
    if match:
        return {"operation": "percent_of", "numbers": (parse_number(match.group("a")), parse_number(match.group("b")))}
    return None


def parse_statistics(text: str) -> dict | None:
    stated = _STATED_COUNT.search(text)
    cleaned = _STATED_COUNT.sub(" ", text) if stated else text
    operations, first_end = [], None
    for match in _OPERATION.finditer(cleaned):
        if match.lastgroup not in operations:
            operations.append(match.lastgroup)
        if first_end is None:
            first_end = match.end()
    if not operations:
        return None
    region = cleaned.split(":", 1)[1] if ":" in cleaned else cleaned[first_end:]
    numbers = extract_numbers(region)
    if len(numbers) < 2:
        return None
    arguments = {"operations": tuple(operations), "numbers": tuple(numbers)}
    if stated:
        arguments["stated_count"] = int(stated.group(1))
    return arguments


def numbers_rule(original: str, body: str) -> RouteDecision | None:
    arguments = parse_percentage(body) or parse_statistics(body)
    if arguments is None:
        return None
    if _EXPLAIN.search(body):
        arguments["explain"] = True
    return RouteDecision(
        Intent.NUMBERS, 0.95, targets.NUMBERS, arguments,
        "The message asks for a calculation on numbers it contains.",
    )
