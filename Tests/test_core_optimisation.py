"""Phase 7: every optimisation must give the reference implementation's results."""

import json

import numpy as np
import pytest

from aurora_core.generation import GenerationConfig
from aurora_core.generation.generate import TextStream
from aurora_core.generation.sampling import FastSampler, sample_next
from aurora_core.runtime import AuroraRuntime, RuntimeLimits
from aurora_core.runtime.quantize import (
    channel_axis,
    decode_params,
    dequantize_int8,
    encode_params,
    quantize_int8,
)
from aurora_core.runtime.session import FastInferenceSession, InferenceSession
from aurora_core.tokenizer import BOS_ID, EOS_ID, PAD_ID, UNK_ID, ByteBPETokenizer, CharTokenizer
from aurora_core.training import CheckpointError, load_checkpoint, save_checkpoint
from aurora_core.training.optim import AdamW
from aurora_core.transformer import AuroraT0, T0Config

SMALL = dict(vocab_size=50, context_length=8, d_model=12, n_heads=3, n_layers=2, d_ff=20, init_std=0.3)


# ------------------------------------------------------------- text stream


@pytest.mark.parametrize("make", [
    lambda: ByteBPETokenizer.train(["héllo wörld 🚀 नमस्ते " * 20], vocab_size=300),
    lambda: CharTokenizer("abc é"),
])
def test_incremental_text_stream_equals_decode(make):
    """Random ids, including broken UTF-8 and every special token."""
    tok = make()
    rng = np.random.default_rng(0)
    for _ in range(1000):
        ids = [int(x) for x in rng.integers(0, tok.vocab_size, rng.integers(0, 25))]
        ids += [int(x) for x in rng.choice([PAD_ID, UNK_ID, BOS_ID, EOS_ID], size=rng.integers(0, 3))]
        rng.shuffle(ids)
        stream = TextStream(tok)
        pieces = [stream.add(i) for i in ids]
        assert stream.text == tok.decode(ids)
        pieces.append(stream.finish())
        assert "".join(pieces) == tok.decode(ids)


def test_text_stream_never_shows_half_a_character():
    tok = ByteBPETokenizer()
    stream = TextStream(tok)
    rocket = [4 + b for b in "🚀".encode()]
    assert [stream.add(t) for t in rocket] == ["", "", "", "🚀"]


def test_text_stream_cost_does_not_grow_with_length(monkeypatch):
    tok = ByteBPETokenizer()
    calls = []
    monkeypatch.setattr(tok, "decode", lambda ids, **k: calls.append(len(ids)) or "")
    stream = TextStream(tok)
    for _ in range(500):
        stream.add(4 + ord("a"))
    assert calls == []                       # never re-decodes the whole text


def test_text_stream_rejects_bad_ids():
    with pytest.raises(ValueError):
        TextStream(ByteBPETokenizer()).add(260)


# ---------------------------------------------------------------- session


@pytest.mark.parametrize("dtype,tolerance", [("float64", 1e-12), ("float32", 1e-5)])
@pytest.mark.parametrize("keep", [1, 3, 8])
def test_fast_session_equals_reference_session(dtype, tolerance, keep):
    model = AuroraT0(T0Config(**SMALL, dtype=dtype), seed=1)
    seq = [int(t) for t in np.random.default_rng(keep).integers(0, 50, 40)]
    ref, fast = InferenceSession(model, keep=keep), FastInferenceSession(model, keep=keep)
    np.testing.assert_allclose(fast.start(seq[:3]), ref.start(seq[:3]), atol=tolerance)
    for token in seq[3:]:
        out = fast.append(token)
        assert out.dtype == model.config.np_dtype
        np.testing.assert_allclose(out, ref.append(token), atol=tolerance, rtol=tolerance)
        assert fast.tokens == ref.tokens
    assert fast.prefills == ref.prefills


def test_fast_session_equals_full_forward_pass():
    model = AuroraT0(T0Config(**SMALL, dtype="float64"), seed=4)
    seq = [int(t) for t in np.random.default_rng(9).integers(0, 50, 8)]
    session = FastInferenceSession(model)
    session.start(seq[:1])
    for i in range(1, 8):
        np.testing.assert_allclose(session.append(seq[i]), model(np.array(seq[:i + 1]))[-1], atol=1e-12)


# ---------------------------------------------------------------- sampling


@pytest.mark.parametrize("options", [
    dict(temperature=0),
    dict(temperature=1.0),
    dict(temperature=0.5, top_k=40),
    dict(temperature=0.8, top_k=1),
    dict(temperature=1.2, top_p=0.9),
    dict(temperature=0.7, top_k=10, top_p=0.5),
    dict(temperature=1.0, top_k=600),                  # larger than the vocabulary
])
def test_fast_sampler_chooses_the_same_tokens(options):
    rng = np.random.default_rng(0)
    banned = (PAD_ID, UNK_ID, BOS_ID)
    sampler = FastSampler(512, banned=banned, **options)
    a, b = np.random.default_rng(1), np.random.default_rng(1)
    for _ in range(300):
        logits = rng.normal(scale=3, size=512).astype(np.float32)
        assert sampler(logits, b) == sample_next(logits, a, banned=banned, **options)


def test_fast_sampler_keeps_ties_like_the_reference():
    logits = np.array([0.0, 1.0, 1.0, 1.0, -1.0])
    sampler = FastSampler(5, temperature=1.0, top_k=2)
    a, b = np.random.default_rng(3), np.random.default_rng(3)
    for _ in range(200):
        assert sampler(logits, b) == sample_next(logits, a, temperature=1.0, top_k=2)


def test_fast_sampler_validates_settings():
    with pytest.raises(ValueError):
        FastSampler(10, temperature=-1)
    with pytest.raises(ValueError):
        FastSampler(10, top_p=0)
    with pytest.raises(ValueError):
        FastSampler(3, banned=(0, 1, 2))


# ------------------------------------------------------------------ runtime


@pytest.fixture
def checkpoint(tmp_path):
    tok = ByteBPETokenizer()
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=16, d_model=16, n_heads=2), seed=2)
    path = tmp_path / "m.npz"
    save_checkpoint(path, model, tokenizer=tok)
    return path


@pytest.mark.parametrize("config", [
    GenerationConfig(max_new_tokens=80, temperature=0, top_k=None, stop_at_eos=False),
    GenerationConfig(max_new_tokens=80, temperature=0.5, top_k=40, seed=3, stop_at_eos=False),
    GenerationConfig(max_new_tokens=80, temperature=1.0, top_k=None, top_p=0.9, seed=4, stop_at_eos=False),
])
def test_fast_engine_generates_exactly_what_the_reference_engine_does(checkpoint, config):
    texts = {}
    for engine in ("reference", "fast"):
        rt = AuroraRuntime(RuntimeLimits(engine=engine))
        rt.load(checkpoint)
        result = rt.generate("Hello", config)
        texts[engine] = (result.token_ids, result.text, result.stats.cache_rebuilds)
    assert texts["fast"] == texts["reference"]


def test_fast_engine_is_the_default_and_engines_are_validated():
    assert RuntimeLimits().engine == "fast"
    with pytest.raises(ValueError):
        RuntimeLimits(engine="turbo")


# ------------------------------------------------------- lower precision


def test_int8_round_trip_error_is_at_most_half_a_step():
    w = np.random.default_rng(0).normal(size=(64, 32)).astype(np.float32)
    for axis in (0, 1):
        q, scale = quantize_int8(w, axis)
        assert q.dtype == np.int8 and np.abs(q).max() <= 127
        assert scale.shape == ((1, 32) if axis == 0 else (64, 1))
        assert np.all(np.abs(dequantize_int8(q, scale) - w) <= scale / 2 + 1e-7)


def test_int8_handles_zero_channels():
    w = np.zeros((4, 3), dtype=np.float32)
    w[:, 1] = [1, -2, 3, 0.5]
    q, scale = quantize_int8(w, 0)
    np.testing.assert_array_equal(dequantize_int8(q, scale)[:, [0, 2]], 0)


def test_channels():
    assert channel_axis("token_embedding") == 1 and channel_axis("position_embedding") == 1
    assert channel_axis("blocks.0.attn.w_qkv") == 0


def test_only_matrices_are_reduced():
    params = AuroraT0(T0Config(**SMALL), seed=0).params
    stored = encode_params(params, "int8")
    assert "qparam/blocks.0.ffn.w_in" in stored and "param/blocks.0.ffn.b_in" in stored
    assert stored["param/ln_f.gamma"].dtype == np.float32
    half = encode_params(params, "float16")
    assert half["param/token_embedding"].dtype == np.float16 and half["param/ln_f.beta"].dtype == np.float32
    with pytest.raises(ValueError):
        encode_params(params, "int4")
    with pytest.raises(ValueError):
        decode_params({"qparam/x": np.zeros((2, 2), dtype=np.int8)})   # no scale


@pytest.mark.parametrize("precision,tolerance,ratio", [("float16", 1e-3, 0.6), ("int8", 2e-2, 0.4)])
def test_reduced_precision_checkpoints(tmp_path, precision, tolerance, ratio):
    tok = ByteBPETokenizer()
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=16, d_model=64, n_heads=2, d_ff=128), seed=0)
    save_checkpoint(tmp_path / "full.npz", model, tokenizer=tok)
    save_checkpoint(tmp_path / "small.npz", model, tokenizer=tok, precision=precision)
    loaded = load_checkpoint(tmp_path / "small.npz").model
    assert all(v.dtype == np.float32 for v in loaded.params.values())
    tokens = np.random.default_rng(0).integers(0, tok.vocab_size, 16)
    np.testing.assert_allclose(loaded(tokens), model(tokens), atol=tolerance)
    size = (tmp_path / "small.npz").stat().st_size / (tmp_path / "full.npz").stat().st_size
    assert size < ratio
    rt = AuroraRuntime()
    rt.load(tmp_path / "small.npz")
    # Fixed seed and no early stop: an untrained model sometimes samples the end
    # token at random, which made this check fail about one run in ten.
    config = GenerationConfig(max_new_tokens=5, seed=0, stop_at_eos=False)
    assert rt.generate("Hi", config).stats.new_tokens == 5


def test_reduced_precision_refuses_optimizer_state(tmp_path):
    model = AuroraT0(T0Config(**SMALL))
    with pytest.raises(ValueError):
        save_checkpoint(tmp_path / "x.npz", model, optimizer=AdamW(model.params), precision="int8")


def test_unknown_precision_in_a_file_is_rejected(tmp_path):
    save_checkpoint(tmp_path / "m.npz", AuroraT0(T0Config(**SMALL)))
    with np.load(tmp_path / "m.npz") as archive:
        arrays = {k: archive[k] for k in archive.files}
    header = json.loads(str(arrays["header"]))
    header["precision"] = "int2"
    arrays["header"] = np.array(json.dumps(header))
    np.savez(tmp_path / "m.npz", **arrays)
    with pytest.raises(CheckpointError):
        load_checkpoint(tmp_path / "m.npz")


def test_old_checkpoints_without_precision_still_load(tmp_path):
    save_checkpoint(tmp_path / "m.npz", AuroraT0(T0Config(**SMALL)))
    with np.load(tmp_path / "m.npz") as archive:
        arrays = {k: archive[k] for k in archive.files}
    header = json.loads(str(arrays["header"]))
    del header["precision"]
    arrays["header"] = np.array(json.dumps(header))
    np.savez(tmp_path / "m.npz", **arrays)
    load_checkpoint(tmp_path / "m.npz")


# ------------------------------------------------------------ tools


def test_precision_comparison(tmp_path):
    from aurora_core.runtime.precision import compare_precisions

    text = "".join(f"The cat sat on mat number {i % 7}.\n\n" for i in range(400))
    tok = ByteBPETokenizer.train([text], vocab_size=300)
    save_checkpoint(tmp_path / "m.npz", AuroraT0(T0Config(vocab_size=tok.vocab_size), seed=0), tokenizer=tok)
    rows = compare_precisions(tmp_path / "m.npz", text)
    assert [r["precision"] for r in rows] == ["float32", "float16", "int8"]
    assert rows[0]["greedy_agreement"] == 1.0 and rows[0]["max_weight_error"] == 0
    assert rows[2]["file_bytes"] < rows[1]["file_bytes"] < rows[0]["file_bytes"]
    assert (tmp_path / "m_int8.npz").is_file()


def test_benchmark_reports_identical_tokens(checkpoint):
    from aurora_core.runtime.benchmark import benchmark_model

    r = benchmark_model(checkpoint, tokens=20, repeats=1)
    assert r["identical_tokens"] and r["fast"] > 0 and r["reference"] > 0 and r["no_cache"] > 0
