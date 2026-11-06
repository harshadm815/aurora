"""Phase 4: backpropagation, data, optimiser, checkpoints and the training loop."""

import json
import math

import numpy as np
import pytest

from aurora_core.tensors import check_gradient
from aurora_core.tokenizer import ByteBPETokenizer, CharTokenizer
from aurora_core.training import (
    AdamW,
    CheckpointError,
    TokenDataset,
    TrainConfig,
    TrainingDiverged,
    bigram_loss,
    clip_grad_norm,
    evaluate,
    global_grad_norm,
    learning_rate,
    load_checkpoint,
    save_checkpoint,
    split_text,
    strip_gutenberg,
    train,
    unigram_loss,
    uniform_loss,
)
from aurora_core.transformer import AuroraT0, T0Config
from aurora_core.transformer.backprop import forward_logits, loss_and_gradients

TINY = dict(vocab_size=11, context_length=6, d_model=8, n_heads=2, n_layers=2, d_ff=12)


@pytest.fixture
def rng():
    return np.random.default_rng(0)


# ------------------------------------------------------------ backpropagation


def test_training_forward_matches_model_forward(rng):
    cfg = T0Config(**TINY, dtype="float64")
    model = AuroraT0(cfg, seed=1)
    tokens = rng.integers(0, 11, size=(3, 6))
    np.testing.assert_allclose(forward_logits(cfg, model.params, tokens), model(tokens), rtol=0, atol=1e-12)


@pytest.mark.parametrize("ignore", [None, -1])
def test_every_parameter_gradient_matches_numerical_gradient(rng, ignore):
    """The whole backward pass, checked entry by entry for every parameter
    of a 2-layer model (large init so no gradient is trivially ~0)."""
    cfg = T0Config(**TINY, dtype="float64", init_std=0.5)
    model = AuroraT0(cfg, seed=3)
    tokens = rng.integers(0, 11, size=(2, 5))
    targets = rng.integers(0, 11, size=(2, 5))
    if ignore is not None:
        targets[0, 2] = ignore
    loss, grads = loss_and_gradients(model, tokens, targets, ignore_index=ignore)
    assert loss == pytest.approx(model.loss(tokens, targets) if ignore is None else loss)
    assert grads.keys() == model.params.keys()
    for name, param in model.params.items():
        result = check_gradient(lambda: loss_and_gradients(model, tokens, targets, ignore)[0], param, grads[name])
        assert result.passed, (name, result)


def test_gradients_for_a_full_context_and_unused_positions(rng):
    cfg = T0Config(**TINY, dtype="float64", init_std=0.5)
    model = AuroraT0(cfg, seed=4)
    tokens = rng.integers(0, 11, size=(1, 3))          # shorter than the context
    targets = rng.integers(0, 11, size=(1, 3))
    _, grads = loss_and_gradients(model, tokens, targets)
    # Positions 3-5 weren't used, so their embeddings get no gradient.
    assert np.all(grads["position_embedding"][3:] == 0)
    assert np.any(grads["position_embedding"][:3] != 0)
    # Tokens that appear neither as input nor as a scored output still get
    # gradient through the tied output layer (every logit is scored).
    assert np.all(np.isfinite(grads["token_embedding"]))


def test_gradients_keep_the_model_dtype(rng):
    model = AuroraT0(T0Config(**TINY), seed=0)
    _, grads = loss_and_gradients(model, rng.integers(0, 11, (2, 6)), rng.integers(0, 11, (2, 6)))
    assert all(g.dtype == np.float32 for g in grads.values())


def test_loss_and_gradients_validate_inputs():
    model = AuroraT0(T0Config(**TINY), seed=0)
    with pytest.raises(ValueError):
        loss_and_gradients(model, np.zeros((2, 4), dtype=int), np.zeros((2, 3), dtype=int))
    with pytest.raises(ValueError):
        loss_and_gradients(model, np.full((1, 4), 99), np.zeros((1, 4), dtype=int))


# ----------------------------------------------------------------------- data


def test_strip_gutenberg():
    raw = ("Header\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\n\nThe story.\n\n"
           "*** END OF THE PROJECT GUTENBERG EBOOK X ***\nLicence")
    assert strip_gutenberg(raw) == "The story.\n"
    assert strip_gutenberg("plain text") == "plain text"


def test_split_text_is_contiguous_and_complete():
    text = "".join(f"Paragraph {i} " * 20 + "\n\n" for i in range(200))
    s = split_text(text)
    assert s.train + s.validation + s.test == text
    assert 0.85 < len(s.train) / len(text) < 0.95
    assert s.validation.startswith("Paragraph") and s.test.startswith("Paragraph")


def test_split_text_validation():
    with pytest.raises(ValueError):
        split_text("abc" * 100, validation=0.6, test=0.5)
    with pytest.raises(ValueError):
        split_text("ab")


def test_token_dataset_batches_shift_targets_by_one(rng):
    data = TokenDataset(np.arange(100), context_length=8)
    x, y = data.random_batch(rng, 5)
    assert x.shape == y.shape == (5, 8)
    np.testing.assert_array_equal(y, x + 1)
    assert x.max() < 100 and y.max() < 100


def test_token_dataset_windows_are_fixed_and_non_overlapping():
    data = TokenDataset(np.arange(50), context_length=8)
    x, y = data.windows()
    assert x[:, 0].tolist() == [0, 8, 16, 24, 32, 40]
    np.testing.assert_array_equal(y, x + 1)
    np.testing.assert_array_equal(data.windows()[0], x)
    assert len(data.windows(max_windows=3)[0]) == 3


def test_token_dataset_rejects_short_data():
    with pytest.raises(ValueError):
        TokenDataset(np.arange(8), context_length=8)


# ------------------------------------------------------------------ baselines


def test_baselines():
    assert uniform_loss(10) == pytest.approx(math.log(10))
    train_ids = np.array([0, 1] * 50)
    # Unigram: tokens 0 and 1 each about half the time.
    assert unigram_loss(train_ids, np.array([0, 1]), vocab_size=2, smoothing=0) == pytest.approx(math.log(2))
    # Bigram: 1 always follows 0 and vice versa, so it is nearly certain.
    loss = bigram_loss(train_ids, np.array([0, 1]), np.array([1, 0]), vocab_size=2, smoothing=0.01)
    assert loss < 0.01
    # Unseen tokens are smoothed, not infinite.
    assert math.isfinite(unigram_loss(train_ids, np.array([5]), vocab_size=6))


# ------------------------------------------------------------------ optimiser


def test_adamw_first_step_matches_hand_calculation():
    params = {"w": np.array([[1.0, -2.0]]), "b": np.array([0.5])}
    grads = {"w": np.array([[0.1, -0.3]]), "b": np.array([0.2])}
    opt = AdamW(params, lr=0.01, weight_decay=0.1)
    opt.step(params, grads)
    # After one step, m̂ = g and v̂ = g², so the Adam update is lr·sign(g)
    # (up to eps); decay shrinks matrices only.
    np.testing.assert_allclose(params["w"], [[1.0 * (1 - 0.001) - 0.01, -2.0 * (1 - 0.001) + 0.01]], rtol=1e-6)
    np.testing.assert_allclose(params["b"], [0.5 - 0.01], rtol=1e-6)


def test_adamw_minimises_a_quadratic():
    target = np.array([[3.0, -1.0], [0.5, 2.0]])
    params = {"w": np.zeros((2, 2))}
    opt = AdamW(params, lr=0.1, weight_decay=0.0)
    for step in range(500):
        lr = learning_rate(step, max_lr=0.1, warmup_steps=10, total_steps=500, min_lr=1e-4)
        opt.step(params, {"w": 2 * (params["w"] - target)}, lr=lr)
    np.testing.assert_allclose(params["w"], target, atol=1e-3)


def test_adamw_decays_only_matrices():
    model = AuroraT0(T0Config(**TINY), seed=0)
    opt = AdamW(model.params)
    assert "token_embedding" in opt.decayed and "blocks.0.attn.w_qkv" in opt.decayed
    assert "blocks.0.attn.b_qkv" not in opt.decayed and "ln_f.gamma" not in opt.decayed


def test_adamw_state_round_trip():
    params = {"w": np.ones((2, 2))}
    a = AdamW(params)
    a.step(params, {"w": np.full((2, 2), 0.5)})
    b = AdamW(params)
    b.load_state(a.state())
    assert b.t == 1
    np.testing.assert_array_equal(b.m["w"], a.m["w"])
    with pytest.raises(ValueError):
        AdamW({"w": np.ones(3)}).load_state(a.state())


def test_adamw_rejects_bad_settings():
    with pytest.raises(ValueError):
        AdamW({"w": np.ones(2)}, lr=0)
    with pytest.raises(ValueError):
        AdamW({"w": np.ones(2)}, betas=(1.0, 0.9))


def test_gradient_clipping():
    grads = {"a": np.array([3.0]), "b": np.array([4.0])}
    assert global_grad_norm(grads) == pytest.approx(5.0)
    assert clip_grad_norm(grads, 1.0) == pytest.approx(5.0)
    assert global_grad_norm(grads) == pytest.approx(1.0)
    np.testing.assert_allclose(grads["a"] / grads["b"], 0.75)     # direction kept
    small = {"a": np.array([0.1])}
    clip_grad_norm(small, 1.0)
    assert small["a"][0] == 0.1


def test_learning_rate_schedule():
    lr = lambda s: learning_rate(s, max_lr=1.0, warmup_steps=10, total_steps=110, min_lr=0.1)  # noqa: E731
    assert lr(0) == pytest.approx(0.1)
    assert lr(9) == pytest.approx(1.0)
    assert lr(10) == pytest.approx(1.0)
    assert lr(60) == pytest.approx(0.55)
    assert lr(110) == pytest.approx(0.1) and lr(500) == pytest.approx(0.1)
    decay = [lr(s) for s in range(10, 111)]
    assert all(a >= b for a, b in zip(decay, decay[1:]))


# ---------------------------------------------------------------- checkpoints


def test_checkpoint_round_trip(tmp_path, rng):
    tok = CharTokenizer("abcdefg")
    model = AuroraT0(T0Config(**TINY), seed=2)
    opt = AdamW(model.params)
    _, grads = loss_and_gradients(model, rng.integers(0, 11, (2, 6)), rng.integers(0, 11, (2, 6)))
    opt.step(model.params, grads)
    path = tmp_path / "ckpt" / "model.npz"
    save_checkpoint(path, model, step=17, optimizer=opt, tokenizer=tok, metadata={"note": "test"})

    ckpt = load_checkpoint(path)
    assert ckpt.step == 17 and ckpt.metadata == {"note": "test"}
    assert ckpt.model.config == model.config
    for name, value in model.params.items():
        np.testing.assert_array_equal(ckpt.model.params[name], value)
    assert ckpt.optimizer_state["t"] == 1
    np.testing.assert_array_equal(ckpt.optimizer_state["m"]["ln_f.gamma"], opt.m["ln_f.gamma"])
    assert ckpt.tokenizer.encode("bad") == tok.encode("bad")
    assert not list(tmp_path.rglob("*.tmp"))
    tokens = rng.integers(0, 11, size=6)
    np.testing.assert_array_equal(ckpt.model(tokens), model(tokens))


def test_checkpoint_without_optimizer_or_tokenizer(tmp_path):
    save_checkpoint(tmp_path / "m.npz", AuroraT0(T0Config(**TINY)))
    ckpt = load_checkpoint(tmp_path / "m.npz")
    assert ckpt.optimizer_state is None and ckpt.tokenizer is None


def test_checkpoint_rejects_bad_files(tmp_path):
    with pytest.raises(CheckpointError):
        load_checkpoint(tmp_path / "missing.npz")
    (tmp_path / "junk.npz").write_bytes(b"not a zip file")
    with pytest.raises(CheckpointError):
        load_checkpoint(tmp_path / "junk.npz")
    np.savez(tmp_path / "other.npz", header=np.array(json.dumps({"format": "other"})))
    with pytest.raises(CheckpointError):
        load_checkpoint(tmp_path / "other.npz")
    # Pickled objects are refused: loading never runs code.
    np.savez(tmp_path / "pickled.npz", header=np.array([{"format": "aurora-checkpoint"}], dtype=object))
    with pytest.raises(CheckpointError):
        load_checkpoint(tmp_path / "pickled.npz")


def test_checkpoint_rejects_mismatched_tokenizer(tmp_path):
    save_checkpoint(tmp_path / "m.npz", AuroraT0(T0Config(**TINY)), tokenizer=ByteBPETokenizer())
    with pytest.raises(CheckpointError):
        load_checkpoint(tmp_path / "m.npz")


def test_checkpoint_rejects_a_damaged_parameter(tmp_path):
    model = AuroraT0(T0Config(**TINY))
    save_checkpoint(tmp_path / "m.npz", model)
    with np.load(tmp_path / "m.npz") as archive:
        arrays = {k: archive[k] for k in archive.files}
    arrays["param/ln_f.beta"] = np.zeros(3, dtype=np.float32)
    np.savez(tmp_path / "m.npz", **arrays)
    with pytest.raises(CheckpointError):
        load_checkpoint(tmp_path / "m.npz")


# -------------------------------------------------------------- training loop


def pattern_data(context):
    """Text with a learnable rule: the alphabet, repeated."""
    tok = CharTokenizer("abcdefghij")
    ids = np.array(tok.encode("abcdefghij" * 60))
    return tok, TokenDataset(ids[:500], context), TokenDataset(ids[500:], context)


def test_training_learns_a_pattern_and_beats_the_baselines():
    tok, train_data, val_data = pattern_data(8)
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=8, d_model=16, n_heads=2, d_ff=32), seed=0)
    start = evaluate(model, val_data)
    cfg = TrainConfig(steps=150, batch_size=16, max_lr=1e-2, min_lr=1e-3, warmup_steps=10, eval_interval=50)
    result = train(model, train_data, val_data, cfg)

    assert [h["step"] for h in result.history] == [50, 100, 150]
    assert result.history[-1]["train_loss"] < result.history[0]["train_loss"]
    assert result.best_validation_loss < 0.1 < start
    x, y = val_data.windows()
    assert result.best_validation_loss < unigram_loss(train_data.ids, y, tok.vocab_size)
    # The rule is deterministic, so the model should get every next letter right.
    assert np.all(model(x).argmax(-1) == y)


def test_training_is_reproducible():
    results = []
    for _ in range(2):
        tok, train_data, val_data = pattern_data(8)
        model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=8), seed=1)
        cfg = TrainConfig(steps=20, batch_size=4, warmup_steps=2, eval_interval=10)
        results.append(train(model, train_data, val_data, cfg).history)
    assert [h["validation_loss"] for h in results[0]] == [h["validation_loss"] for h in results[1]]


def test_best_parameters_are_kept(monkeypatch):
    tok, train_data, val_data = pattern_data(8)
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=8), seed=0)
    cfg = TrainConfig(steps=40, batch_size=8, warmup_steps=2, eval_interval=10)
    result = train(model, train_data, val_data, cfg)
    best = AuroraT0(model.config, params=result.best_params)
    assert evaluate(best, val_data) == pytest.approx(result.best_validation_loss)
    assert result.best_validation_loss == min(h["validation_loss"] for h in result.history)


def test_divergence_is_detected(monkeypatch):
    import aurora_core.training.trainer as trainer_module

    tok, train_data, val_data = pattern_data(8)
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=8), seed=0)
    monkeypatch.setattr(trainer_module, "loss_and_gradients", lambda *a, **k: (float("nan"), {}))
    with pytest.raises(TrainingDiverged):
        train(model, train_data, val_data, TrainConfig(steps=5, batch_size=2, warmup_steps=1, eval_interval=5))


def test_training_rejects_windows_longer_than_the_model_context():
    tok, train_data, val_data = pattern_data(16)
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=8))
    with pytest.raises(ValueError):
        train(model, train_data, val_data, TrainConfig(steps=1))


def test_train_config_validation():
    with pytest.raises(ValueError):
        TrainConfig(steps=0)
    with pytest.raises(ValueError):
        TrainConfig(min_lr=1.0, max_lr=0.1)


def test_command_line_training_end_to_end(tmp_path):
    from aurora_core.training.__main__ import main

    text = "".join(f"The cat sat on mat number {i % 7}. The dog ran.\n\n" for i in range(400))
    data = tmp_path / "data.txt"
    data.write_text(text, encoding="utf-8")
    out = tmp_path / "out"
    assert main(["--data", str(data), "--out", str(out), "--vocab-size", "300",
                 "--steps", "60", "--eval-interval", "30", "--batch-size", "8"]) == 0

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    # 60 steps only show the pipeline works end to end (better than random);
    # learning quality is tested on the pattern above.
    assert summary["test_loss"] < summary["test_baselines"]["uniform"]
    lines = (out / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["step"] for line in lines] == [30, 60]
    best = load_checkpoint(out / "best.npz")
    assert best.tokenizer.vocab_size == best.model.config.vocab_size
    assert load_checkpoint(out / "last.npz").optimizer_state is not None
    assert (out / "tokenizer.json").is_file()
