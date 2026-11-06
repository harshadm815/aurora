"""AURORA-T0: a small decoder-only Transformer written with NumPy only."""

from aurora_core.transformer.config import T0Config
from aurora_core.transformer.model import AuroraT0, ForwardResult
from aurora_core.transformer.parameters import (
    count_parameters,
    init_parameters,
    parameter_shapes,
    validate_parameters,
)

__all__ = [
    "AuroraT0",
    "ForwardResult",
    "T0Config",
    "count_parameters",
    "init_parameters",
    "parameter_shapes",
    "validate_parameters",
]
