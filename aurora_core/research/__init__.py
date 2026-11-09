"""
AURORA research (blueprint Phase 16): architecture variants and
reproducible experiments.

- ``arch``: one-change variants of AURORA-T0 (RMSNorm, SwiGLU, a gated
  linear recurrence instead of attention), parameter-matched, with their
  own gradient-checked backward pass.
- ``stats``: paired comparisons over seeds with 95 % confidence intervals.
- ``runner``: specification -> runs -> analysis -> report, with data and
  code fingerprints, resumable runs and bit-for-bit verification.
- ``claims``: the register of what has been shown, with its evidence.
"""

from aurora_core.research.arch import ArchConfig, VariantModel, matched
from aurora_core.research.stats import paired

__all__ = ["ArchConfig", "VariantModel", "matched", "paired"]
