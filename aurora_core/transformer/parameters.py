"""
AURORA-T0's weights: names, shapes and random initialisation.

Parameters are kept in a flat dict of named NumPy arrays, e.g.
"blocks.0.attn.w_qkv". Flat names make saving, loading, counting and
(later) applying gradients straightforward.

Initialisation follows GPT-2: weights ~ Normal(0, init_std), biases 0,
layer-norm scales 1 and shifts 0, and the two projections that write
into the residual stream are scaled down by 1/sqrt(2 * n_layers) so the
residual stream doesn't grow with depth.

The output layer reuses the token embedding (weight tying), so there is
no separate (d_model, vocab) matrix.
"""

import math

import numpy as np

from aurora_core.transformer.config import T0Config


def parameter_shapes(config: T0Config) -> dict[str, tuple[int, ...]]:
    """Every parameter's name and shape, in a fixed order."""
    d, f = config.d_model, config.d_ff
    shapes: dict[str, tuple[int, ...]] = {
        "token_embedding": (config.vocab_size, d),
        "position_embedding": (config.context_length, d),
    }
    for i in range(config.n_layers):
        p = f"blocks.{i}."
        shapes.update({
            p + "ln1.gamma": (d,),
            p + "ln1.beta": (d,),
            p + "attn.w_qkv": (d, 3 * d),
            p + "attn.b_qkv": (3 * d,),
            p + "attn.w_out": (d, d),
            p + "attn.b_out": (d,),
            p + "ln2.gamma": (d,),
            p + "ln2.beta": (d,),
            p + "ffn.w_in": (d, f),
            p + "ffn.b_in": (f,),
            p + "ffn.w_out": (f, d),
            p + "ffn.b_out": (d,),
        })
    shapes["ln_f.gamma"] = (d,)
    shapes["ln_f.beta"] = (d,)
    return shapes


def init_parameters(config: T0Config, seed: int = 0) -> dict[str, np.ndarray]:
    """Random initial parameters. The same seed always gives the same model."""
    rng = np.random.default_rng(seed)
    residual_scale = 1.0 / math.sqrt(2 * config.n_layers)
    params: dict[str, np.ndarray] = {}
    for name, shape in parameter_shapes(config).items():
        leaf = name.rsplit(".", 1)[-1]
        if leaf == "gamma":
            value = np.ones(shape)
        elif leaf == "beta" or leaf.startswith("b_"):
            value = np.zeros(shape)
        else:
            std = config.init_std
            if name.endswith(("attn.w_out", "ffn.w_out")):
                std *= residual_scale
            value = rng.normal(0.0, std, size=shape)
        params[name] = value.astype(config.np_dtype)
    return params


def count_parameters(params: dict[str, np.ndarray]) -> int:
    return int(sum(p.size for p in params.values()))


def validate_parameters(config: T0Config, params: dict[str, np.ndarray]) -> None:
    """Raise ValueError unless `params` has exactly the expected names and shapes."""
    expected = parameter_shapes(config)
    missing = expected.keys() - params.keys()
    extra = params.keys() - expected.keys()
    if missing or extra:
        raise ValueError(f"parameter names differ: missing {sorted(missing)}, unexpected {sorted(extra)}")
    for name, shape in expected.items():
        if params[name].shape != shape:
            raise ValueError(f"{name} has shape {params[name].shape}, expected {shape}")
        if not np.all(np.isfinite(params[name])):
            raise ValueError(f"{name} contains NaN or infinity")
