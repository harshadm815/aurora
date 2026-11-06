"""Phase 5: sampling, text generation, quality measures and experiments."""

import json

import numpy as np
import pytest

from aurora_core.generation import (
    BigramSampler,
    GenerationConfig,
    Reference,
    average_stats,
    generate_ids,
    generate_text,
    next_token_distribution,
    sample_next,
    stream_ids,
    text_stats,
)
from aurora_core.generation.generate import BANNED
from aurora_core.tokenizer import BOS_ID, EOS_ID, PAD_ID, UNK_ID, ByteBPETokenizer, CharTokenizer
from aurora_core.training import TokenDataset, TrainConfig, train
from aurora_core.training.experiments import run_experiments
from aurora_core.transformer import AuroraT0, T0Config

LOGITS = np.array([2.0, 1.0, 0.5, 0.0, -1.0])


# ------------------------------------------------------------------ sampling


def test_greedy_picks_the_highest_score():
    probs = next_token_distribution(LOGITS, temperature=0)
    assert probs.tolist() == [1, 0, 0, 0, 0]
    assert sample_next(LOGITS, np.random.default_rng(0), temperature=0) == 0


def test_temperature_sharpens_and_flattens():
    cold = next_token_distribution(LOGITS, temperature=0.5)
    normal = next_token_distribution(LOGITS, temperature=1.0)
    hot = next_token_distribution(LOGITS, temperature=2.0)
    assert cold[0] > normal[0] > hot[0]
    for p in (cold, normal, hot):
        assert p.sum() == pytest.approx(1.0)


def test_top_k_keeps_only_k_tokens():
    probs = next_token_distribution(LOGITS, top_k=2)
    assert np.count_nonzero(probs) == 2 and probs[0] > probs[1] > 0
    assert probs.sum() == pytest.approx(1.0)
    # top_k larger than the vocabulary changes nothing.
    np.testing.assert_allclose(next_token_distribution(LOGITS, top_k=99), next_token_distribution(LOGITS))


def test_top_p_keeps_the_smallest_set_reaching_p():
    full = next_token_distribution(LOGITS)
    probs = next_token_distribution(LOGITS, top_p=0.6)
    kept = np.flatnonzero(probs)
    assert kept.tolist() == [0, 1]                    # 0.53 alone < 0.6; adding 0.20 reaches it
    assert full[0] < 0.6 <= full[0] + full[1]
    assert next_token_distribution(LOGITS, top_p=0.01).tolist() == [1, 0, 0, 0, 0]


def test_banned_tokens_get_no_probability():
    probs = next_token_distribution(LOGITS, banned=(0, 1))
    assert probs[0] == probs[1] == 0
    assert next_token_distribution(LOGITS, temperature=0, banned=(0,)).argmax() == 1
    with pytest.raises(ValueError):
        next_token_distribution(LOGITS, banned=tuple(range(5)))


def test_sampling_follows_the_distribution():
    rng = np.random.default_rng(0)
    counts = np.bincount([sample_next(LOGITS, rng) for _ in range(20_000)], minlength=5) / 20_000
    np.testing.assert_allclose(counts, next_token_distribution(LOGITS), atol=0.01)


@pytest.mark.parametrize("options", [{"temperature": -1}, {"top_k": 0}, {"top_p": 0}, {"top_p": 1.5}])
def test_invalid_sampling_options(options):
    with pytest.raises(ValueError):
        next_token_distribution(LOGITS, **options)
    with pytest.raises(ValueError):
        GenerationConfig(**options)


# ---------------------------------------------------------------- generation


@pytest.fixture(scope="module")
def trained():
    """A tiny model trained on a repeating sentence: generation must
    reproduce it, which proves the learned weights drive the output."""
    text = "the cat sat on a mat. " * 80
    tok = CharTokenizer.train([text])
    ids = np.array(tok.encode(text))
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=16, d_model=24, n_heads=2, d_ff=48), seed=0)
    cfg = TrainConfig(steps=300, batch_size=16, max_lr=1e-2, min_lr=1e-3, warmup_steps=20, eval_interval=300)
    train(model, TokenDataset(ids[:1500], 16), TokenDataset(ids[1500:], 16), cfg)
    return model, tok


def test_trained_model_generates_what_it_learned(trained):
    model, tok = trained
    out = generate_text(model, tok, "the cat ", GenerationConfig(max_new_tokens=40, temperature=0))
    assert out.startswith("sat on a mat. the cat sat on a mat.")


def test_untrained_model_does_not(trained):
    _, tok = trained
    untrained = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=16, d_model=24, n_heads=2, d_ff=48), seed=0)
    out = generate_text(untrained, tok, "the cat ", GenerationConfig(max_new_tokens=40, temperature=0))
    assert not out.startswith("sat on a mat.")


def test_generation_runs_past_the_context_length(trained):
    model, tok = trained
    ids = generate_ids(model, tok.encode("the "), GenerationConfig(max_new_tokens=100, temperature=0))
    assert len(ids) == 100                     # context is 16; a sliding window is used
    assert "the cat sat on a mat." in tok.decode(ids)


def test_generation_is_reproducible_with_a_seed(trained):
    model, tok = trained
    cfg = GenerationConfig(max_new_tokens=30, temperature=1.5, top_k=None, seed=7)
    a = generate_ids(model, tok.encode("the"), cfg)
    assert a == generate_ids(model, tok.encode("the"), cfg)
    assert a != generate_ids(model, tok.encode("the"), GenerationConfig(max_new_tokens=30, temperature=1.5,
                                                                          top_k=None, seed=8))


def test_generation_never_writes_structural_tokens(trained):
    original, tok = trained
    model = AuroraT0(original.config, params={k: v.copy() for k, v in original.params.items()})
    # Make the structural tokens the most likely by far.
    for tid in BANNED:
        model.params["token_embedding"][tid] *= 0
    ids = generate_ids(model, tok.encode("the"), GenerationConfig(max_new_tokens=50, temperature=2.0, seed=0))
    assert not set(ids) & {PAD_ID, UNK_ID, BOS_ID}


def test_generation_stops_at_eos():
    tok = CharTokenizer("ab")

    class EndsImmediately(AuroraT0):
        def __call__(self, tokens):
            logits = np.zeros((len(tokens), tok.vocab_size), dtype=np.float32)
            logits[:, EOS_ID] = 10
            return logits

    model = EndsImmediately(T0Config(vocab_size=tok.vocab_size, context_length=4))
    assert generate_ids(model, [4], GenerationConfig(temperature=0)) == []
    assert len(generate_ids(model, [4], GenerationConfig(temperature=0, stop_at_eos=False, max_new_tokens=3))) == 3


def test_generation_needs_a_prompt_and_a_matching_tokenizer(trained):
    model, tok = trained
    with pytest.raises(ValueError):
        generate_ids(model, [], GenerationConfig())
    with pytest.raises(ValueError):
        generate_text(model, ByteBPETokenizer(), "the", GenerationConfig())
    assert generate_ids(model, [4], GenerationConfig(max_new_tokens=0)) == []


def test_streamed_text_equals_the_final_text():
    tok = ByteBPETokenizer()
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=8), seed=3)
    pieces = []
    cfg = GenerationConfig(max_new_tokens=60, temperature=1.0, top_k=None, seed=1)
    final = generate_text(model, tok, "Hi", cfg, on_text=pieces.append)
    assert "".join(pieces) == final
    # Byte-level output of an untrained model contains broken UTF-8, so
    # some pieces were held back until they were complete (or the end).
    assert all(p for p in pieces)


def test_stream_ids_is_lazy(trained):
    model, tok = trained
    stream = stream_ids(model, tok.encode("the"), GenerationConfig(max_new_tokens=10**9, temperature=0))
    assert len([next(stream) for _ in range(5)]) == 5


# ------------------------------------------------------------- quality measures


def test_text_stats():
    ref = Reference("the dog sat on the mat and the cat sat on the hat")
    s = text_stats("The dog sat on the mat zzq", ref)
    assert s["words"] == 7
    assert s["known_word_rate"] == pytest.approx(6 / 7)
    assert s["copied_6gram_rate"] == pytest.approx(1 / 2)   # "the dog sat on the mat" is copied
    loop = text_stats("a b c d a b c d a b c d", ref)
    assert loop["repeated_4gram_rate"] == pytest.approx(5 / 9)
    assert loop["distinct_2"] == pytest.approx(4 / 11)
    assert text_stats("", ref)["words"] == 0


def test_curly_apostrophes_count_as_words():
    ref = Reference("I don't know")
    assert text_stats("I don’t know", ref)["known_word_rate"] == 1.0


def test_average_stats():
    ref = Reference("a b c")
    avg = average_stats(["a b c", "x y z"], ref)
    assert avg["known_word_rate"] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        average_stats([], ref)


def test_bigram_sampler_follows_the_training_pairs():
    train_ids = np.array([4, 5, 6] * 200)
    sampler = BigramSampler(train_ids, vocab_size=8, smoothing=1e-6)
    out = sampler.generate([4], 9, np.random.default_rng(0), banned=BANNED)
    assert out == [5, 6, 4, 5, 6, 4, 5, 6, 4]


# ---------------------------------------------------------------- experiments


def test_experiment_runner_compares_variants():
    tok = CharTokenizer("abcdefgh")
    ids = np.array(tok.encode("abcdefgh" * 100))
    base = T0Config(vocab_size=tok.vocab_size, context_length=8, d_model=8, d_ff=16)
    seen = []
    results = run_experiments(
        ids[:600], ids[600:], tok.vocab_size,
        [{"name": "base"}, {"name": "ctx 4", "model": {"context_length": 4}},
         {"name": "low lr", "train": {"max_lr": 1e-4, "min_lr": 1e-5}}],
        steps=30, base_model=base, base_train=TrainConfig(steps=30, eval_interval=15, warmup_steps=3),
        tokens_per_step=64, on_result=seen.append,
    )
    assert [r["name"] for r in results] == ["base", "ctx 4", "low lr"] == [r["name"] for r in seen]
    by_name = {r["name"]: r for r in results}
    assert by_name["base"]["batch_size"] == 8 and by_name["ctx 4"]["batch_size"] == 16   # equal tokens per step
    assert by_name["low lr"]["best_validation_loss"] > by_name["base"]["best_validation_loss"]
    assert by_name["low lr"]["changes"] == {"max_lr": 1e-4, "min_lr": 1e-5}
    json.dumps(results)                     # results can be saved as JSON


def test_experiment_command_saves_each_result_and_resumes(tmp_path, monkeypatch):
    import aurora_core.training.experiments as experiments

    data = tmp_path / "data.txt"
    data.write_text("".join(f"Line {i % 9} of a small test text.\n\n" for i in range(300)), encoding="utf-8")
    out = tmp_path / "results.json"
    args = ["--data", str(data), "--out", str(out), "--steps", "4", "--vocab-size", "270"]

    calls = []
    real = experiments.run_experiments

    def counting(*a, **k):
        calls.append([e["name"] for e in a[3]])
        return real(*a, **k)

    monkeypatch.setattr(experiments, "run_experiments", counting)
    assert experiments.main(args + ["--only", "T0 baseline", "context 16"]) == 0
    assert [r["name"] for r in json.loads(out.read_text())["results"]] == ["T0 baseline", "context 16"]

    # A second run keeps finished experiments and only runs the new one.
    assert experiments.main(args + ["--only", "T0 baseline", "context 16", "2 layers"]) == 0
    assert calls[-1] == ["2 layers"]
    assert [r["name"] for r in json.loads(out.read_text())["results"]] == ["T0 baseline", "context 16", "2 layers"]

    # Different settings start again from scratch.
    assert experiments.main(args[:4] + ["--steps", "5", "--vocab-size", "270", "--only", "T0 baseline"]) == 0
    assert calls[-1] == ["T0 baseline"]
    assert not list(tmp_path.glob("*.tmp"))


def test_experiment_list_changes_one_setting_each():
    from aurora_core.training.experiments import EXPERIMENTS

    names = [e["name"] for e in EXPERIMENTS]
    assert names[0] == "T0 baseline" and len(set(names)) == len(names)
    for e in EXPERIMENTS[1:]:
        changes = {**e.get("model", {}), **e.get("train", {})}
        # One idea per experiment (a learning-rate change sets max and min together;
        # a wider model widens the feed-forward layer with it).
        assert 1 <= len(changes) <= 2, e["name"]
