"""
Statistics for comparing training runs, with NumPy only.

Runs are *paired*: a variant and the baseline trained with the same seed
see the same batches in the same order, so the per-seed difference
removes most of the luck of the draw. The question "is the variant
better?" becomes "is the mean paired difference below zero, by more than
chance would explain?", answered with a 95 % confidence interval from
Student's t distribution.

A claim is only made when the decision rule written in the experiment's
specification (before any run) is met; otherwise the result is reported
as "no detectable difference", which is a finding too.
"""

import math
from dataclasses import asdict, dataclass

import numpy as np

# Two-sided 95 % critical values of Student's t, by degrees of freedom.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
        10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
        19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052,
        28: 2.048, 29: 2.045, 30: 2.042}

BETTER, WORSE, SAME = "better", "worse", "no detectable difference"


def t_critical(df: int) -> float:
    if df < 1:
        raise ValueError("need at least two runs for a confidence interval")
    return _T95.get(df, 1.96)


@dataclass(frozen=True)
class Comparison:
    n: int
    mean_difference: float        # variant - baseline (negative: variant has lower loss)
    std_difference: float
    ci_low: float
    ci_high: float
    wins: int                     # seeds where the variant was lower
    decision: str
    differences: tuple[float, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["differences"] = list(self.differences)
        return data


def paired(variant: list[float], baseline: list[float], min_effect: float = 0.0) -> Comparison:
    """95 % confidence interval of the mean of (variant - baseline), paired by position.

    Decision: "better" if the interval lies entirely below zero *and* the
    mean improvement is at least ``min_effect``; "worse" if the interval
    lies entirely above zero; otherwise "no detectable difference"."""
    if len(variant) != len(baseline):
        raise ValueError("paired comparison needs the same number of runs on each side")
    differences = np.asarray(variant, dtype=float) - np.asarray(baseline, dtype=float)
    n = len(differences)
    mean = float(differences.mean())
    std = float(differences.std(ddof=1)) if n > 1 else math.nan
    half = t_critical(n - 1) * std / math.sqrt(n)
    low, high = mean - half, mean + half
    if high < 0 and -mean >= min_effect:
        decision = BETTER
    elif low > 0:
        decision = WORSE
    else:
        decision = SAME
    return Comparison(n, mean, std, low, high, int((differences < 0).sum()), decision,
                      tuple(float(d) for d in differences))


def against_constant(values: list[float], constant: float, min_effect: float = 0.0) -> Comparison:
    """Is the mean of `values` below a fixed reference (e.g. the bigram baseline)?"""
    return paired(list(values), [constant] * len(values), min_effect)


def summary(values: list[float]) -> dict:
    array = np.asarray(values, dtype=float)
    n = len(array)
    std = float(array.std(ddof=1)) if n > 1 else math.nan
    half = t_critical(n - 1) * std / math.sqrt(n) if n > 1 else math.nan
    return {"n": n, "mean": float(array.mean()), "std": std, "ci_low": float(array.mean() - half),
            "ci_high": float(array.mean() + half), "min": float(array.min()), "max": float(array.max())}
