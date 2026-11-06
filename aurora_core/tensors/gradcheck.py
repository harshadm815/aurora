"""
Numerical gradient checking.

An analytic gradient (from a backward function) is compared with a
numerical estimate from central differences:

    df/dx_i  ~=  (f(x + eps e_i) - f(x - eps e_i)) / (2 eps)

Use float64 arrays: in float32 the rounding error of the difference is
larger than the quantity being measured.
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class GradCheckResult:
    """Outcome of a gradient check.

    Passes when every entry satisfies
    |analytic - numeric| <= atol + rtol * |numeric|
    (the same rule as numpy.allclose). A purely relative test would fail
    on entries whose true gradient is ~0, where finite differences can
    only be accurate to about 1e-9 in absolute terms.
    """

    max_abs_error: float
    max_relative_error: float
    passed: bool


def numerical_gradient(
    f: Callable[[], float], x: np.ndarray, eps: float = 1e-6
) -> np.ndarray:
    """Central-difference gradient of the scalar f() with respect to x.

    f takes no arguments and must read x, which is changed in place one
    entry at a time and always restored, even if f raises.
    """
    if x.dtype != np.float64:
        raise ValueError("numerical_gradient needs a float64 array for accuracy")
    grad = np.zeros_like(x)
    it = np.nditer(x, flags=["multi_index"])
    for _ in it:
        i = it.multi_index
        original = x[i]
        try:
            x[i] = original + eps
            plus = f()
            x[i] = original - eps
            minus = f()
        finally:
            x[i] = original
        grad[i] = (plus - minus) / (2.0 * eps)
    return grad


def relative_error(a: np.ndarray, b: np.ndarray, floor: float = 1e-8) -> float:
    """Largest element-wise |a - b| / max(|a| + |b|, floor).

    The floor stops entries where both gradients are ~0 from producing a
    meaningless huge ratio.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape != b.shape:
        raise ValueError(f"shapes differ: {a.shape} vs {b.shape}")
    denom = np.maximum(np.abs(a) + np.abs(b), floor)
    return float(np.max(np.abs(a - b) / denom)) if a.size else 0.0


def check_gradient(
    f: Callable[[], float],
    x: np.ndarray,
    analytic: np.ndarray,
    eps: float = 1e-6,
    rtol: float = 1e-5,
    atol: float = 1e-7,
) -> GradCheckResult:
    """Compare an analytic gradient of f() with respect to x against the
    numerical estimate."""
    analytic = np.asarray(analytic, dtype=np.float64)
    if analytic.shape != x.shape:
        raise ValueError(f"analytic gradient shape {analytic.shape} != x shape {x.shape}")
    numeric = numerical_gradient(f, x, eps)
    diff = np.abs(analytic - numeric)
    return GradCheckResult(
        max_abs_error=float(diff.max()) if diff.size else 0.0,
        max_relative_error=relative_error(analytic, numeric),
        passed=bool(np.all(diff <= atol + rtol * np.abs(numeric))),
    )
