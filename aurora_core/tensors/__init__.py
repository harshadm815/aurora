"""Tensor mathematics for AURORA's neural network (NumPy only)."""

from aurora_core.tensors.gradcheck import (
    GradCheckResult,
    check_gradient,
    numerical_gradient,
    relative_error,
)
from aurora_core.tensors.ops import (
    cross_entropy,
    cross_entropy_backward,
    gelu,
    gelu_backward,
    layer_norm,
    layer_norm_backward,
    linear,
    linear_backward,
    log_softmax,
    log_softmax_backward,
    softmax,
    softmax_backward,
)

__all__ = [
    "GradCheckResult",
    "check_gradient",
    "cross_entropy",
    "cross_entropy_backward",
    "gelu",
    "gelu_backward",
    "layer_norm",
    "layer_norm_backward",
    "linear",
    "linear_backward",
    "log_softmax",
    "log_softmax_backward",
    "numerical_gradient",
    "relative_error",
    "softmax",
    "softmax_backward",
]
