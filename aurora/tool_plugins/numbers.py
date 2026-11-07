"""
Numbers tool: statistics and percentages, computed exactly.

    average / mean, median, mode, sum / total, count, minimum, maximum,
    range, standard deviation (population and sample)
    p % of x,  what percent x is of y,  increase / decrease x by p %

The language model is never asked to do arithmetic: TinyLlama gets sums
wrong. Results come from Python's `statistics` module (which averages
with exact fractions), and explanations are built from templates filled
with the computed values, so every number in a reply is one that was
actually calculated.

Limits: at most MAX_NUMBERS numbers, each at most MAX_MAGNITUDE in size.
"""

import math
import statistics
from dataclasses import dataclass, field

from aurora.tool_plugins.calculator import format_number
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

MAX_NUMBERS = 1000
MAX_MAGNITUDE = 1e15
LIST_IN_FULL = 12            # longer lists are summarised in explanations
NEWLINE = chr(10)

STATISTICS = ("mean", "median", "mode", "sum", "count", "min", "max", "range", "stdev")
PERCENTAGES = ("percent_of", "what_percent", "increase", "decrease")
OPERATIONS = STATISTICS + PERCENTAGES

NAMES = {
    "mean": "average (mean)", "median": "median", "mode": "mode", "sum": "sum",
    "count": "count", "min": "smallest value", "max": "largest value", "range": "range",
    "stdev": "standard deviation",
}


class NumbersError(ValueError):
    """The request can't be calculated; the message says why."""


@dataclass
class Outcome:
    value: float | int | None          # None when there is no single answer (several modes)
    summary: str                       # one-line answer
    explanation: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def fmt(value) -> str:
    return format_number(value)


def _listing(numbers) -> str:
    if len(numbers) <= LIST_IN_FULL:
        return " + ".join(fmt(n) for n in numbers)
    return f"{fmt(numbers[0])} + {fmt(numbers[1])} + … + {fmt(numbers[-1])}"


def _clean_numbers(numbers) -> list:
    if not isinstance(numbers, (list, tuple)):
        raise NumbersError("I need a list of numbers.")
    if len(numbers) > MAX_NUMBERS:
        raise NumbersError(f"That's more than {MAX_NUMBERS} numbers; I can handle up to {MAX_NUMBERS}.")
    result = []
    for n in numbers:
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n):
            raise NumbersError("Every value must be an ordinary number.")
        if abs(n) > MAX_MAGNITUDE:
            raise NumbersError("One of the numbers is too large.")
        result.append(int(n) if isinstance(n, float) and n.is_integer() else n)
    return result


def _tidy(value):
    """Exact results as int where possible; floats rounded to 12 significant digits
    so 0.1 + 0.2 gives 0.3."""
    if isinstance(value, int):
        return value
    value = float(f"{float(value):.12g}")
    return int(value) if value.is_integer() and abs(value) < 1e15 else value


def compute_statistic(operation: str, numbers) -> Outcome:
    numbers = _clean_numbers(numbers)
    n = len(numbers)
    need = 1 if operation in ("count", "sum", "min", "max", "mean", "median", "mode") else 2
    if n < need:
        raise NumbersError(f"I need at least {need} number{'s' if need > 1 else ''} for the {NAMES[operation]}.")
    these = f"these {n} numbers" if n > 1 else "this number"

    if operation == "count":
        return Outcome(n, f"There {'are' if n != 1 else 'is'} {n} number{'s' if n != 1 else ''}.",
                       details={"count": n})

    total = _tidy(statistics.fsum(numbers) if any(isinstance(x, float) for x in numbers) else sum(numbers))
    if operation == "sum":
        return Outcome(total, f"The sum of {these} is {fmt(total)}.",
                       [f"{_listing(numbers)} = {fmt(total)}."], {"count": n, "sum": total})

    if operation == "mean":
        mean = _tidy(statistics.mean(numbers))
        median = _tidy(statistics.median(numbers))
        explanation = [
            f"Add them up: {_listing(numbers)} = {fmt(total)}.",
            f"Divide by how many there are: {fmt(total)} ÷ {n} = {fmt(mean)}.",
        ]
        below = sum(1 for x in numbers if x < mean)
        above = sum(1 for x in numbers if x > mean)
        spread = max(numbers) - min(numbers)
        if n >= 3 and spread > 0 and abs(mean - median) > 0.1 * spread:
            if mean > median:
                extreme, side, word = max(numbers), "up", "largest"
            else:
                extreme, side, word = min(numbers), "down", "smallest"
            explanation.append(
                f"The median (middle value) is {fmt(median)}, so the average is pulled {side} by "
                f"{fmt(extreme)}, the {word} value: {below} of the {n} numbers are below the average "
                f"and {above} above it."
            )
        return Outcome(mean, f"The average of {these} is {fmt(mean)}.", explanation,
                       {"count": n, "sum": total, "mean": mean, "median": median, "below": below, "above": above})

    if operation == "median":
        ordered = sorted(numbers)
        median = _tidy(statistics.median(ordered))
        shown = ", ".join(fmt(x) for x in ordered) if n <= LIST_IN_FULL else f"{fmt(ordered[0])}, …, {fmt(ordered[-1])}"
        if n % 2:
            how = f"With an odd count ({n}), the median is the middle value: {fmt(median)}."
        else:
            a, b = ordered[n // 2 - 1], ordered[n // 2]
            how = (f"With an even count ({n}), the median is the average of the two middle values: "
                   f"({fmt(a)} + {fmt(b)}) ÷ 2 = {fmt(median)}.")
        return Outcome(median, f"The median of {these} is {fmt(median)}.", [f"In order: {shown}.", how],
                       {"count": n, "median": median})

    if operation == "mode":
        modes = statistics.multimode(numbers)
        times = numbers.count(modes[0])
        if times == 1:
            return Outcome(None, f"There is no mode: each of the {n} numbers appears once.",
                           details={"count": n, "modes": []})
        if len(modes) == 1:
            return Outcome(modes[0], f"The mode of {these} is {fmt(modes[0])}.",
                           [f"{fmt(modes[0])} appears {times} times, more than any other value."],
                           {"count": n, "modes": modes, "times": times})
        listed = ", ".join(fmt(m) for m in modes)
        return Outcome(None, f"There are {len(modes)} modes: {listed}.",
                       [f"Each of them appears {times} times."], {"count": n, "modes": modes, "times": times})

    if operation in ("min", "max", "range"):
        low, high = min(numbers), max(numbers)
        if operation == "min":
            return Outcome(low, f"The smallest of {these} is {fmt(low)}.",
                           [f"They range from {fmt(low)} to {fmt(high)}."], {"count": n, "min": low, "max": high})
        if operation == "max":
            return Outcome(high, f"The largest of {these} is {fmt(high)}.",
                           [f"They range from {fmt(low)} to {fmt(high)}."], {"count": n, "min": low, "max": high})
        spread = _tidy(high - low)
        return Outcome(spread, f"The range of {these} is {fmt(spread)}.",
                       [f"Largest minus smallest: {fmt(high)} − {fmt(low)} = {fmt(spread)}."],
                       {"count": n, "min": low, "max": high})

    if operation == "stdev":
        mean = _tidy(statistics.mean(numbers))
        population = _tidy(statistics.pstdev(numbers))
        sample = _tidy(statistics.stdev(numbers))
        return Outcome(population, f"The standard deviation of {these} is {fmt(population)}.", [
            f"It measures how far the numbers typically are from their average, {fmt(mean)}: "
            f"the square root of the average squared distance from it.",
            f"That treats them as the whole population. If they're a sample of a larger group, "
            f"the sample standard deviation (dividing by {n - 1} instead of {n}) is {fmt(sample)}.",
        ], {"count": n, "mean": mean, "population": population, "sample": sample})

    raise NumbersError(f"I don't know how to calculate '{operation}'.")


def compute_percentage(operation: str, a, b) -> Outcome:
    a, b = _clean_numbers([a, b])
    if operation == "percent_of":            # a % of b
        value = _tidy(b * a / 100)
        return Outcome(value, f"{fmt(a)}% of {fmt(b)} is {fmt(value)}.",
                       [f"{fmt(b)} × {fmt(a)} ÷ 100 = {fmt(value)}."], {"percent": a, "of": b})
    if operation == "what_percent":          # a is what % of b
        if b == 0:
            raise NumbersError("I can't take a percentage of zero.")
        value = _tidy(a / b * 100)
        return Outcome(value, f"{fmt(a)} is {fmt(value)}% of {fmt(b)}.",
                       [f"{fmt(a)} ÷ {fmt(b)} × 100 = {fmt(value)}."], {"part": a, "whole": b})
    if operation in ("increase", "decrease"):  # change a by b %
        change = _tidy(a * b / 100)
        value = _tidy(a + change if operation == "increase" else a - change)
        sign = "+" if operation == "increase" else "−"
        return Outcome(value, f"{fmt(a)} {operation}d by {fmt(b)}% is {fmt(value)}.",
                       [f"{fmt(b)}% of {fmt(a)} is {fmt(change)}; {fmt(a)} {sign} {fmt(change)} = {fmt(value)}."],
                       {"start": a, "percent": b, "change": change})
    raise NumbersError(f"I don't know how to calculate '{operation}'.")


class NumbersTool(BaseTool):
    """Needs no permissions: it only does arithmetic on numbers it is given."""

    def __init__(self):
        super().__init__("Numbers")

    def execute(self, operation: str = "", numbers=(), explain: bool = False,
                stated_count: int | None = None, operations=(), **_) -> ToolResult:
        """One calculation (operation=...) or several on the same numbers
        (operations=("mean", "median"))."""
        operations = tuple(operations) or (operation,)
        numbers = tuple(numbers)
        counts_matter = any(op in STATISTICS for op in operations)
        if stated_count is not None and counts_matter and stated_count != len(numbers):
            found = ", ".join(fmt(x) for x in numbers) or "none"
            return ToolResult(False, f"You mentioned {stated_count} numbers, but I found "
                                     f"{len(numbers)}: {found}. Which numbers did you mean?",
                              {"problem": "count_mismatch", "stated": stated_count, "found": len(numbers)})
        outcomes = {}
        try:
            for op in operations:
                if op in STATISTICS:
                    outcomes[op] = compute_statistic(op, list(numbers))
                elif op in PERCENTAGES:
                    if len(numbers) != 2:
                        raise NumbersError("I need exactly two numbers for that.")
                    outcomes[op] = compute_percentage(op, *numbers)
                else:
                    raise NumbersError("I'm not sure which calculation you want.")
        except NumbersError as error:
            return ToolResult(False, f"I couldn't calculate that: {error}")

        blocks = []
        for outcome in outcomes.values():
            block = [outcome.summary]
            if explain and outcome.explanation:
                block += outcome.explanation
            blocks.append(NEWLINE.join(block))
        # A blank line after an answer that has an explanation, and between calculations.
        separator = NEWLINE * 2 if explain or len(blocks) > 1 else NEWLINE
        text = separator.join(blocks) if len(blocks) > 1 else (
            blocks[0].replace(NEWLINE, NEWLINE * 2, 1) if explain else blocks[0])
        first = next(iter(outcomes.values()))
        return ToolResult(True, text, {
            "operation": operations[0],
            "operations": operations,
            "value": first.value,
            "values": {op: o.value for op, o in outcomes.items()},
            "numbers": list(numbers),
            "details": {op: o.details for op, o in outcomes.items()},
            "explained": bool(explain and any(o.explanation for o in outcomes.values())),
        })
