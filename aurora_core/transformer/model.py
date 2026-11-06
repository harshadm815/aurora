"""AURORA-T0: a small decoder-only Transformer (forward pass)."""

from dataclasses import dataclass

import numpy as np

from aurora_core.tensors.ops import cross_entropy, layer_norm
from aurora_core.transformer.config import T0Config
from aurora_core.transformer.layers import embed, transformer_block
from aurora_core.transformer.parameters import (
    count_parameters,
    init_parameters,
    validate_parameters,
)


@dataclass
class ForwardResult:
    logits: np.ndarray                 # (B, T, V), or (T, V) for a 1-D input
    attention: list[np.ndarray]        # one (B, H, T, T) array per layer


class AuroraT0:
    """token ids -> embeddings -> N decoder blocks -> layer norm -> logits.

    The logits at position t score every vocabulary entry as the next
    token, using only tokens 0..t.
    """

    def __init__(
        self,
        config: T0Config | None = None,
        params: dict[str, np.ndarray] | None = None,
        seed: int = 0,
    ):
        self.config = config or T0Config()
        if params is None:
            params = init_parameters(self.config, seed)
        else:
            validate_parameters(self.config, params)
        self.params = params

    @property
    def num_parameters(self) -> int:
        return count_parameters(self.params)

    def _check_tokens(self, tokens) -> np.ndarray:
        tokens = np.asarray(tokens)
        if tokens.ndim not in (1, 2):
            raise ValueError(f"tokens must be 1-D (T,) or 2-D (B, T), got shape {tokens.shape}")
        if tokens.size == 0 or tokens.shape[-1] == 0:
            raise ValueError("tokens must not be empty")
        if not np.issubdtype(tokens.dtype, np.integer):
            raise ValueError(f"tokens must be integers, got {tokens.dtype}")
        if tokens.shape[-1] > self.config.context_length:
            raise ValueError(
                f"sequence of {tokens.shape[-1]} tokens is longer than the "
                f"context length {self.config.context_length}"
            )
        if tokens.min() < 0 or tokens.max() >= self.config.vocab_size:
            raise ValueError(f"token ids must be in [0, {self.config.vocab_size})")
        return tokens

    def forward(self, tokens) -> ForwardResult:
        tokens = self._check_tokens(tokens)
        single = tokens.ndim == 1
        if single:
            tokens = tokens[None, :]

        cfg, params = self.config, self.params
        x = embed(tokens, params["token_embedding"], params["position_embedding"])
        attention = []
        for i in range(cfg.n_layers):
            x, weights = transformer_block(x, params, f"blocks.{i}.", cfg.n_heads, cfg.layer_norm_eps)
            attention.append(weights)
        x = layer_norm(x, params["ln_f.gamma"], params["ln_f.beta"], cfg.layer_norm_eps)
        logits = x @ params["token_embedding"].T      # weight tying

        if single:
            logits = logits[0]
            attention = [w[0] for w in attention]
        return ForwardResult(logits=logits, attention=attention)

    def __call__(self, tokens) -> np.ndarray:
        """Shorthand for forward(tokens).logits."""
        return self.forward(tokens).logits

    def loss(self, tokens, targets) -> float:
        """Mean next-token cross-entropy of `targets` given `tokens`."""
        return cross_entropy(self(tokens), np.asarray(targets))
