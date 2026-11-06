"""Phase 2: AURORA-T0 forward pass."""

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from aurora_core.tensors.ops import softmax
from aurora_core.transformer import (
    AuroraT0,
    T0Config,
    count_parameters,
    init_parameters,
    parameter_shapes,
)
from aurora_core.transformer.layers import (
    causal_mask,
    causal_self_attention,
    embed,
    merge_heads,
    split_heads,
)

CORE_DIR = Path(__file__).resolve().parents[1] / "aurora_core"


@pytest.fixture
def rng():
    return np.random.default_rng(7)


@pytest.fixture(scope="module")
def model():
    return AuroraT0(T0Config(), seed=0)


# ------------------------------------------------------------- configuration


def test_default_config_is_the_t0_specification():
    cfg = T0Config()
    assert (cfg.n_layers, cfg.n_heads, cfg.d_model, cfg.context_length, cfg.d_ff) == (1, 2, 32, 32, 128)
    assert cfg.head_dim == 16


@pytest.mark.parametrize("kwargs", [
    {"d_model": 30, "n_heads": 4},     # not divisible
    {"n_heads": 0},
    {"vocab_size": -1},
    {"context_length": 2.5},
    {"n_layers": True},
    {"layer_norm_eps": 0},
    {"init_std": -0.1},
    {"dtype": "float16"},
])
def test_invalid_configs_are_rejected(kwargs):
    with pytest.raises(ValueError):
        T0Config(**kwargs)


# --------------------------------------------------------------- parameters


def test_parameter_count():
    # embeddings 256*32 + 32*32; per block: 2 layer norms 2*64,
    # qkv 32*96+96, out 32*32+32, ffn 32*128+128 + 128*32+32; final norm 64.
    expected = 8192 + 1024 + 128 + 3168 + 1056 + 4224 + 4128 + 64
    params = init_parameters(T0Config())
    assert count_parameters(params) == expected == 21_984


def test_parameter_shapes_match_initialisation():
    cfg = T0Config(n_layers=2)
    params = init_parameters(cfg)
    assert list(params) == list(parameter_shapes(cfg))
    assert all(params[name].shape == shape for name, shape in parameter_shapes(cfg).items())
    assert "blocks.1.attn.w_qkv" in params


def test_initialisation_values():
    cfg = T0Config()
    params = init_parameters(cfg, seed=3)
    assert np.all(params["blocks.0.ln1.gamma"] == 1)
    assert np.all(params["blocks.0.ln1.beta"] == 0)
    assert np.all(params["blocks.0.attn.b_qkv"] == 0)
    assert all(p.dtype == np.float32 for p in params.values())
    assert params["token_embedding"].std() == pytest.approx(cfg.init_std, rel=0.05)
    # Residual projections are scaled down by 1/sqrt(2 * n_layers).
    assert params["blocks.0.ffn.w_out"].std() == pytest.approx(cfg.init_std / math.sqrt(2), rel=0.1)


def test_same_seed_same_model_different_seed_different_model():
    a, b, c = (init_parameters(T0Config(), seed=s) for s in (1, 1, 2))
    assert all(np.array_equal(a[k], b[k]) for k in a)
    assert not np.array_equal(a["token_embedding"], c["token_embedding"])


def test_loading_parameters_checks_names_and_shapes():
    cfg = T0Config()
    params = init_parameters(cfg)
    AuroraT0(cfg, params=params)  # valid

    missing = dict(params)
    del missing["ln_f.beta"]
    with pytest.raises(ValueError):
        AuroraT0(cfg, params=missing)

    wrong = dict(params, **{"ln_f.beta": np.zeros(31, dtype=np.float32)})
    with pytest.raises(ValueError):
        AuroraT0(cfg, params=wrong)

    broken = dict(params, **{"ln_f.beta": np.full(32, np.nan, dtype=np.float32)})
    with pytest.raises(ValueError):
        AuroraT0(cfg, params=broken)


# ------------------------------------------------------------ building blocks


def test_embedding_adds_token_and_position_vectors(rng):
    tok = rng.normal(size=(10, 4))
    pos = rng.normal(size=(6, 4))
    tokens = np.array([[3, 3, 7]])
    out = embed(tokens, tok, pos)
    assert out.shape == (1, 3, 4)
    np.testing.assert_allclose(out[0, 1], tok[3] + pos[1])
    # The same token at different positions gets different vectors.
    assert not np.allclose(out[0, 0], out[0, 1])


def test_causal_mask():
    np.testing.assert_array_equal(
        causal_mask(3),
        [[True, False, False], [True, True, False], [True, True, True]],
    )


def test_split_and_merge_heads_are_inverses(rng):
    x = rng.normal(size=(2, 5, 8))
    heads = split_heads(x, 4)
    assert heads.shape == (2, 4, 5, 2)
    np.testing.assert_array_equal(heads[1, 2, 3], x[1, 3, 4:6])
    np.testing.assert_array_equal(merge_heads(heads), x)


def reference_attention(x, w_qkv, b_qkv, w_out, b_out, n_heads):
    """Straightforward loop version of causal attention, written
    independently of layers.py, to check the vectorised one."""
    batch, seq_len, d_model = x.shape
    hd = d_model // n_heads
    out = np.zeros_like(x)
    for b in range(batch):
        qkv = x[b] @ w_qkv + b_qkv
        q, k, v = qkv[:, :d_model], qkv[:, d_model:2 * d_model], qkv[:, 2 * d_model:]
        heads = []
        for h in range(n_heads):
            cols = slice(h * hd, (h + 1) * hd)
            head_out = np.zeros((seq_len, hd))
            for i in range(seq_len):
                scores = np.array([q[i, cols] @ k[j, cols] / math.sqrt(hd) for j in range(i + 1)])
                w = np.exp(scores - scores.max())
                w /= w.sum()
                head_out[i] = sum(w[j] * v[j, cols] for j in range(i + 1))
            heads.append(head_out)
        out[b] = np.concatenate(heads, axis=-1) @ w_out + b_out
    return out


def test_attention_matches_reference_implementation(rng):
    d, h = 8, 2
    x = rng.normal(size=(2, 5, d))
    w_qkv, b_qkv = rng.normal(size=(d, 3 * d)), rng.normal(size=3 * d)
    w_out, b_out = rng.normal(size=(d, d)), rng.normal(size=d)
    out, weights = causal_self_attention(x, w_qkv, b_qkv, w_out, b_out, h)
    np.testing.assert_allclose(out, reference_attention(x, w_qkv, b_qkv, w_out, b_out, h), rtol=1e-10, atol=1e-12)
    assert weights.shape == (2, h, 5, 5)


def test_first_token_attends_only_to_itself(rng):
    d = 8
    x = rng.normal(size=(1, 4, d))
    _, weights = causal_self_attention(
        x, rng.normal(size=(d, 3 * d)), np.zeros(3 * d), rng.normal(size=(d, d)), np.zeros(d), 2
    )
    np.testing.assert_allclose(weights[0, :, 0, 0], 1.0)


# ------------------------------------------------------------- full forward


def test_forward_shapes(model):
    cfg = model.config
    batch = np.zeros((3, 10), dtype=np.int64)
    result = model.forward(batch)
    assert result.logits.shape == (3, 10, cfg.vocab_size)
    assert len(result.attention) == cfg.n_layers
    assert result.attention[0].shape == (3, cfg.n_heads, 10, 10)

    single = model.forward(np.arange(5))
    assert single.logits.shape == (5, cfg.vocab_size)
    assert single.attention[0].shape == (cfg.n_heads, 5, 5)


def test_forward_output_is_finite_and_float32(model, rng):
    tokens = rng.integers(0, 256, size=(2, 32))
    logits = model(tokens)
    assert logits.dtype == np.float32
    assert np.all(np.isfinite(logits))


def test_full_context_is_accepted_and_longer_is_rejected(model):
    model(np.zeros(32, dtype=np.int64))
    with pytest.raises(ValueError):
        model(np.zeros(33, dtype=np.int64))


@pytest.mark.parametrize("bad", [
    np.array([1, 256]),                 # outside the vocabulary
    np.array([-1, 2]),
    np.array([1.0, 2.0]),               # not integers
    np.array([], dtype=np.int64),       # empty
    np.zeros((2, 0), dtype=np.int64),
    np.zeros((1, 2, 3), dtype=np.int64),  # too many dimensions
])
def test_invalid_tokens_are_rejected(model, bad):
    with pytest.raises(ValueError):
        model(bad)


def test_attention_weights_are_probabilities_over_the_past(model, rng):
    tokens = rng.integers(0, 256, size=(2, 12))
    weights = model.forward(tokens).attention[0]
    np.testing.assert_allclose(weights.sum(axis=-1), 1.0, rtol=1e-5)
    assert np.all(weights >= 0)
    future = ~causal_mask(12)
    assert np.all(weights[..., future] == 0.0)


def test_future_tokens_never_change_earlier_outputs(model, rng):
    """The defining property of a causal language model."""
    tokens = rng.integers(0, 256, size=20)
    base = model(tokens)
    for t in (0, 7, 19):
        changed = tokens.copy()
        changed[t] = (changed[t] + 1) % 256
        out = model(changed)
        np.testing.assert_array_equal(out[:t], base[:t])
        assert not np.allclose(out[t], base[t])


def test_prefix_gives_the_same_outputs(model, rng):
    tokens = rng.integers(0, 256, size=16)
    np.testing.assert_allclose(model(tokens[:9]), model(tokens)[:9], rtol=1e-5, atol=1e-6)


def test_batch_items_are_independent(model, rng):
    batch = rng.integers(0, 256, size=(3, 8))
    together = model(batch)
    for i in range(3):
        np.testing.assert_allclose(together[i], model(batch[i]), rtol=1e-5, atol=1e-6)


def test_forward_is_deterministic(model, rng):
    tokens = rng.integers(0, 256, size=10)
    np.testing.assert_array_equal(model(tokens), model(tokens))


def test_untrained_loss_is_close_to_random_guessing(model, rng):
    tokens = rng.integers(0, 256, size=(4, 32))
    targets = rng.integers(0, 256, size=(4, 32))
    assert model.loss(tokens, targets) == pytest.approx(math.log(256), abs=0.1)


def test_untrained_predictions_are_nearly_uniform(model, rng):
    probs = softmax(model(rng.integers(0, 256, size=16)).astype(np.float64))
    assert probs.max() < 5.0 / 256


def test_float64_model_matches_float32_model(rng):
    m32 = AuroraT0(T0Config(dtype="float32"), seed=5)
    m64 = AuroraT0(T0Config(dtype="float64"), seed=5)
    tokens = rng.integers(0, 256, size=12)
    out64 = m64(tokens)
    assert out64.dtype == np.float64
    np.testing.assert_allclose(m32(tokens), out64, rtol=1e-4, atol=1e-5)


def test_larger_configurations_work(rng):
    cfg = T0Config(vocab_size=100, context_length=16, d_model=24, n_heads=3, n_layers=3, d_ff=48)
    model = AuroraT0(cfg, seed=1)
    result = model.forward(rng.integers(0, 100, size=(2, 16)))
    assert result.logits.shape == (2, 16, 100)
    assert len(result.attention) == 3


# ------------------------------------------------------------- architecture


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_aurora_core_uses_only_numpy_and_the_standard_library():
    allowed = {
        "numpy", "aurora_core",
        # standard library
        "abc", "argparse", "collections", "dataclasses", "heapq", "json", "math", "os",
        "pathlib", "re", "sys", "time", "typing", "unicodedata",
    }
    files = list(CORE_DIR.rglob("*.py"))
    assert files
    for path in files:
        extra = imported_modules(path) - allowed
        assert not extra, f"{path.name} imports {extra}"
