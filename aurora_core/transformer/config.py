"""Hyperparameters of AURORA-T0."""

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class T0Config:
    """Size and shape of an AURORA-T0 model.

    The defaults are the AURORA-T0 specification: 1 layer, 2 heads,
    32-dimensional embeddings, context of 32 tokens, feed-forward of 128.

    vocab_size is 256 (one token per byte) as a placeholder until the
    tokenizer of Phase 3 decides the real vocabulary.
    """

    vocab_size: int = 256
    context_length: int = 32
    d_model: int = 32
    n_heads: int = 2
    n_layers: int = 1
    d_ff: int = 128
    layer_norm_eps: float = 1e-5
    init_std: float = 0.02
    dtype: str = "float32"

    def __post_init__(self):
        for name in ("vocab_size", "context_length", "d_model", "n_heads", "n_layers", "d_ff"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        if self.d_model % self.n_heads:
            raise ValueError(
                f"d_model ({self.d_model}) must be divisible by n_heads ({self.n_heads})"
            )
        if not self.layer_norm_eps > 0:
            raise ValueError("layer_norm_eps must be positive")
        if not self.init_std > 0:
            raise ValueError("init_std must be positive")
        if self.dtype not in ("float32", "float64"):
            raise ValueError("dtype must be 'float32' or 'float64'")

    @property
    def head_dim(self) -> int:
        return self.d_model // self.n_heads

    @property
    def np_dtype(self) -> np.dtype:
        return np.dtype(self.dtype)

    def to_dict(self) -> dict:
        return asdict(self)
