"""Phase 6: the AURORA Core Runtime (KV cache, loader, controller, limits)."""

import threading

import numpy as np
import pytest

from aurora_core.generation import GenerationConfig, generate_ids
from aurora_core.runtime import (
    AuroraRuntime,
    InferenceSession,
    ModelLoadError,
    RuntimeBusy,
    RuntimeLimits,
    RuntimeNotReady,
)
from aurora_core.tokenizer import BOS_ID, EOS_ID, PAD_ID, UNK_ID, ByteBPETokenizer, CharTokenizer
from aurora_core.training import TokenDataset, TrainConfig, save_checkpoint, train
from aurora_core.transformer import AuroraT0, T0Config

SMALL = dict(vocab_size=50, context_length=8, d_model=12, n_heads=3, n_layers=2, d_ff=20, init_std=0.3)


@pytest.fixture
def model64():
    return AuroraT0(T0Config(**SMALL, dtype="float64"), seed=1)


@pytest.fixture
def sequence():
    return [int(t) for t in np.random.default_rng(0).integers(0, 50, 40)]


# ------------------------------------------------------------------ KV cache


def test_prefill_matches_the_full_forward_pass(model64, sequence):
    session = InferenceSession(model64)
    for n in (1, 3, 8):
        np.testing.assert_allclose(session.start(sequence[:n]), model64(np.array(sequence[:n]))[-1], atol=1e-12)


def test_cached_steps_match_the_full_forward_pass(model64, sequence):
    session = InferenceSession(model64)
    session.start(sequence[:2])
    for token in sequence[2:8]:
        out = session.append(token)
        np.testing.assert_allclose(out, model64(np.array(session.tokens))[-1], atol=1e-12)
    assert session.prefills == 1        # no rebuild while the context had room


@pytest.mark.parametrize("keep", [1, 3, 4, 8])
def test_after_overflow_the_cache_holds_the_most_recent_tokens(model64, sequence, keep):
    session = InferenceSession(model64, keep=keep)
    session.start(sequence[:5])
    history = list(sequence[:5])
    for token in sequence[5:]:
        out = session.append(token)
        history.append(token)
        assert session.tokens == history[-len(session.tokens):]
        assert 1 <= len(session.tokens) <= 8
        np.testing.assert_allclose(out, model64(np.array(session.tokens))[-1], atol=1e-12)


def test_keeping_the_full_context_equals_the_reference_sliding_window(model64, sequence):
    session = InferenceSession(model64, keep=8)
    session.start(sequence[:3])
    for i in range(3, len(sequence)):
        out = session.append(sequence[i])
        window = np.array(sequence[:i + 1][-8:])
        np.testing.assert_allclose(out, model64(window)[-1], atol=1e-12)


def test_rebuilds_happen_every_context_minus_keep_tokens(model64, sequence):
    session = InferenceSession(model64, keep=4)          # context 8
    session.start(sequence[:8])
    for token in sequence[8:20]:
        session.append(token)
    # Full at 8 tokens; each rebuild leaves 4, so one rebuild per 4 new tokens.
    assert session.prefills == 1 + 3


def test_long_prompts_keep_their_end(model64, sequence):
    session = InferenceSession(model64)
    out = session.start(sequence[:20])
    assert session.truncated == 12 and session.tokens == sequence[12:20]
    np.testing.assert_allclose(out, model64(np.array(sequence[12:20]))[-1], atol=1e-12)


def test_session_validation(model64):
    with pytest.raises(ValueError):
        InferenceSession(model64, keep=0)
    with pytest.raises(ValueError):
        InferenceSession(model64, keep=9)
    session = InferenceSession(model64)
    with pytest.raises(RuntimeError):
        session.append(1)
    with pytest.raises(ValueError):
        session.start([])
    with pytest.raises(ValueError):
        session.start([50])
    session.start([1])
    with pytest.raises(ValueError):
        session.append(-1)


def test_cache_size():
    model = AuroraT0(T0Config(**SMALL), seed=0)
    # 2 (k, v) x 2 layers x 3 heads x 8 positions x 4 dims x 4 bytes
    assert InferenceSession(model).cache.nbytes == 2 * 2 * 3 * 8 * 4 * 4


# ------------------------------------------------------------------- runtime


@pytest.fixture
def checkpoint(tmp_path):
    """A float64 model with a byte-level tokenizer, saved as a checkpoint."""
    tok = ByteBPETokenizer()
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=16, d_model=16, n_heads=2,
                              dtype="float64", init_std=0.2), seed=2)
    path = tmp_path / "model.npz"
    save_checkpoint(path, model, step=12, tokenizer=tok, metadata={"data": ["test"]})
    return path, model, tok


@pytest.fixture
def runtime(checkpoint):
    rt = AuroraRuntime()
    rt.load(checkpoint[0])
    return rt


def test_load_reports_the_model(checkpoint, runtime):
    path, model, tok = checkpoint
    info = runtime.info
    assert info.parameters == model.num_parameters
    assert info.training_step == 12 and info.metadata == {"data": ["test"]}
    assert info.tokenizer == "byte_bpe" and info.config["context_length"] == 16
    import hashlib
    assert info.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    mem = runtime.memory_usage()
    assert mem["parameters"] == sum(p.nbytes for p in model.params.values())
    assert mem["total"] == mem["parameters"] + mem["kv_cache"] > 0


def test_runtime_matches_the_reference_generator(checkpoint):
    """Same seed, same sampling: identical tokens, also past the context
    length when the runtime keeps the full window."""
    path, model, tok = checkpoint
    rt = AuroraRuntime(RuntimeLimits(keep_on_overflow=16))
    rt.load(path)
    for config in (GenerationConfig(max_new_tokens=60, temperature=0, top_k=None, stop_at_eos=False),
                   GenerationConfig(max_new_tokens=60, temperature=1.0, top_k=20, seed=5, stop_at_eos=False)):
        result = rt.generate("Hello there", config)
        assert result.token_ids == generate_ids(model, tok.encode("Hello there"), config)
        assert result.text == tok.decode(result.token_ids)
        assert result.stats.new_tokens == 60 and result.stats.stop_reason == "length"


def test_runtime_with_default_window_agrees_until_the_first_rebuild(checkpoint, runtime):
    path, model, tok = checkpoint
    config = GenerationConfig(max_new_tokens=40, temperature=0, top_k=None, stop_at_eos=False)
    prompt = "Hi"                                       # 2 tokens; the context of 16 fills after 14 more
    result = runtime.generate(prompt, config)
    reference = generate_ids(model, tok.encode(prompt), config)
    assert result.token_ids[:15] == reference[:15]
    assert result.stats.cache_rebuilds >= 1


def test_trained_model_generates_its_text_through_the_runtime(tmp_path):
    text = "the cat sat on a mat. " * 80
    tok = CharTokenizer.train([text])
    ids = np.array(tok.encode(text))
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=16, d_model=24, n_heads=2, d_ff=48), seed=0)
    train(model, TokenDataset(ids[:1500], 16), TokenDataset(ids[1500:], 16),
          TrainConfig(steps=300, batch_size=16, max_lr=1e-2, min_lr=1e-3, warmup_steps=20, eval_interval=300))
    save_checkpoint(tmp_path / "cat.npz", model, tokenizer=tok)

    rt = AuroraRuntime()
    rt.load(tmp_path / "cat.npz")
    result = rt.generate("the cat ", GenerationConfig(max_new_tokens=60, temperature=0))
    assert result.text.startswith("sat on a mat. the cat sat on a mat. the cat sat")


def test_streamed_pieces_join_to_the_result(runtime):
    pieces = []
    result = runtime.generate("Hi", GenerationConfig(max_new_tokens=50, temperature=1.0, top_k=None, seed=0),
                              on_text=pieces.append)
    assert "".join(pieces) == result.text


def test_long_prompt_is_shortened_and_reported(runtime):
    result = runtime.generate("x" * 40, GenerationConfig(max_new_tokens=3, temperature=0))
    assert result.stats.prompt_tokens == 40 and result.stats.prompt_truncated == 24


def make_top(model, *token_ids):
    """Make these tokens score far above all others: the final layer norm
    then outputs a constant vector of ones, and only these tokens'
    embeddings point along it."""
    model.params["ln_f.gamma"][:] = 0.0
    model.params["ln_f.beta"][:] = 1.0
    for tid in token_ids:
        model.params["token_embedding"][tid] = 5.0


def test_structural_tokens_are_never_generated(checkpoint):
    path, model, tok = checkpoint
    make_top(model, PAD_ID, UNK_ID, BOS_ID)
    save_checkpoint(path, model, tokenizer=tok)
    rt = AuroraRuntime()
    rt.load(path)
    logits = rt._session.start(tok.encode("Hi"))
    assert set(np.argsort(logits)[-3:]) == {PAD_ID, UNK_ID, BOS_ID}     # they really are the favourites
    ids = rt.generate("Hi", GenerationConfig(max_new_tokens=40, temperature=2.0, top_k=None, seed=1,
                                             stop_at_eos=False)).token_ids
    assert len(ids) == 40 and not set(ids) & {PAD_ID, UNK_ID, BOS_ID}


def test_stops_at_eos(checkpoint):
    path, model, tok = checkpoint
    make_top(model, EOS_ID)
    save_checkpoint(path, model, tokenizer=tok)
    rt = AuroraRuntime()
    rt.load(path)
    result = rt.generate("Hi", GenerationConfig(max_new_tokens=20, temperature=0))
    assert result.stats.stop_reason == "eos" and result.token_ids == []


def test_cancellation(runtime):
    cancel = threading.Event()
    seen = []

    def on_text(piece):
        seen.append(piece)
        if len(seen) == 3:
            cancel.set()

    result = runtime.generate("Hi", GenerationConfig(max_new_tokens=500, temperature=0, top_k=None,
                                                     stop_at_eos=False), on_text=on_text, cancel=cancel)
    assert result.stats.stop_reason == "cancelled"
    assert result.stats.new_tokens < 500


def test_timeout(checkpoint):
    rt = AuroraRuntime(RuntimeLimits(max_seconds=1.0))
    rt.load(checkpoint[0])
    ticks = iter(range(1000))
    rt.clock = lambda: float(next(ticks)) * 0.3              # each reading is 0.3 s later
    result = rt.generate("Hi", GenerationConfig(max_new_tokens=100, temperature=0, stop_at_eos=False))
    assert result.stats.stop_reason == "timeout" and result.stats.new_tokens < 10


def test_request_limits(checkpoint):
    rt = AuroraRuntime(RuntimeLimits(max_new_tokens=10))
    rt.load(checkpoint[0])
    with pytest.raises(ValueError):
        rt.generate("Hi", GenerationConfig(max_new_tokens=11))
    with pytest.raises(ValueError):
        rt.generate("", GenerationConfig(max_new_tokens=5))
    with pytest.raises(ValueError):
        RuntimeLimits(max_seconds=0)


def test_one_request_at_a_time(runtime):
    started, release = threading.Event(), threading.Event()
    errors = []

    def slow(piece):
        started.set()
        release.wait(5)

    worker = threading.Thread(target=lambda: runtime.generate(
        "Hi", GenerationConfig(max_new_tokens=5, temperature=0, stop_at_eos=False), on_text=slow))
    worker.start()
    assert started.wait(5)
    try:
        with pytest.raises(RuntimeBusy):
            runtime.generate("Hi", GenerationConfig(max_new_tokens=1))
        with pytest.raises(RuntimeBusy):
            runtime.unload()
    finally:
        release.set()
        worker.join(5)
    assert not errors
    runtime.generate("Hi", GenerationConfig(max_new_tokens=1))   # free again


def test_unload_and_not_ready(runtime):
    runtime.unload()
    assert not runtime.loaded
    assert runtime.memory_usage()["total"] == 0
    with pytest.raises(RuntimeNotReady):
        runtime.generate("Hi")
    with pytest.raises(RuntimeNotReady):
        runtime.info


def test_loader_refuses_unusable_files(tmp_path, checkpoint):
    path = checkpoint[0]
    with pytest.raises(ModelLoadError):
        AuroraRuntime().load(tmp_path / "missing.npz")
    with pytest.raises(ModelLoadError):
        AuroraRuntime(RuntimeLimits(max_model_bytes=1000)).load(path)
    (tmp_path / "junk.npz").write_bytes(b"junk")
    with pytest.raises(ModelLoadError):
        AuroraRuntime().load(tmp_path / "junk.npz")
    save_checkpoint(tmp_path / "no_tok.npz", AuroraT0(T0Config(**SMALL)))
    with pytest.raises(ModelLoadError):
        AuroraRuntime().load(tmp_path / "no_tok.npz")


def test_failed_load_keeps_the_previous_model(runtime, tmp_path):
    before = runtime.info.sha256
    with pytest.raises(ModelLoadError):
        runtime.load(tmp_path / "missing.npz")
    assert runtime.info.sha256 == before


def test_command_line(checkpoint, capsys):
    from aurora_core.runtime.__main__ import main

    path = str(checkpoint[0])
    assert main(["--model", path, "--prompt", "Hi", "--tokens", "10", "--seed", "1"]) == 0
    out = capsys.readouterr().out
    assert "sha256" in out and "10 tokens" in out
    assert main(["--model", path, "--benchmark", "--tokens", "20", "--prompt", "Hi"]) == 0
    assert "identical tokens: 20/20" in capsys.readouterr().out
    assert main(["--model", str(checkpoint[0]) + ".missing"]) == 1


def test_runtime_never_uses_ollama_or_the_assistant():
    from pathlib import Path
    for path in (Path(__file__).resolve().parents[1] / "aurora_core" / "runtime").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "ollama" not in source.lower() or path.name == "__init__.py"
        assert "from aurora." not in source and "import aurora." not in source
