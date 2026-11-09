"""Phase 16: research variants, statistics and the reproducible experiment runner."""

import json
import math

import numpy as np
import pytest

from aurora_core.research import arch, claims, runner, stats
from aurora_core.research.arch import ArchConfig, VariantModel, matched, scan, scan_backward
from aurora_core.tensors.gradcheck import check_gradient
from aurora_core.tensors.ops import rms_norm, rms_norm_backward, sigmoid, sigmoid_backward, silu, silu_backward
from aurora_core.training.data import TokenDataset
from aurora_core.training.trainer import TrainConfig, train
from aurora_core.transformer import AuroraT0, T0Config
from aurora_core.transformer.backprop import loss_and_gradients as t0_gradients

SMALL = T0Config(vocab_size=40, context_length=8, d_model=8, n_heads=2, d_ff=16, dtype="float64")


def batch(seed=1, size=(2, 8), vocab=40):
    rng = np.random.default_rng(seed)
    return rng.integers(0, vocab, size=size), rng.integers(0, vocab, size=size)


# --- New operations ---------------------------------------------------------------------------

def test_new_ops_pass_gradient_checks():
    rng = np.random.default_rng(0)
    x, gamma, up = rng.normal(size=(3, 4, 5)), rng.normal(size=5), rng.normal(size=(3, 4, 5))
    dx, dgamma = rms_norm_backward(up, x, gamma)
    assert check_gradient(lambda: float((rms_norm(x, gamma) * up).sum()), x, dx).passed
    assert check_gradient(lambda: float((rms_norm(x, gamma) * up).sum()), gamma, dgamma).passed
    assert check_gradient(lambda: float((silu(x) * up).sum()), x, silu_backward(up, x)).passed
    assert check_gradient(lambda: float((sigmoid(x) * up).sum()), x, sigmoid_backward(up, x)).passed


def test_rms_norm_and_sigmoid_values():
    x = np.array([[3.0, 4.0]])
    assert np.allclose(rms_norm(x, np.ones(2), eps=0.0), x / math.sqrt(12.5))
    assert np.allclose(sigmoid(np.array([-1000.0, 0.0, 1000.0])), [0.0, 0.5, 1.0])
    with pytest.raises(ValueError):
        rms_norm(x, np.ones(3))


# --- Variants ---------------------------------------------------------------------------------

def test_default_variant_is_exactly_t0():
    t0, variant = AuroraT0(SMALL, seed=3), VariantModel(ArchConfig(SMALL), seed=3)
    assert set(t0.params) == set(variant.params)
    assert all(np.array_equal(t0.params[k], variant.params[k]) for k in t0.params)
    x, y = batch()
    assert np.array_equal(t0(x), variant(x))
    loss_t0, grads_t0 = t0_gradients(t0, x, y)
    loss_v, grads_v = variant.loss_and_gradients(x, y)
    assert loss_t0 == loss_v
    assert all(np.array_equal(grads_t0[k], grads_v[k]) for k in grads_t0)


@pytest.mark.parametrize("options", [
    {"norm": "rms"}, {"ffn": "swiglu"}, {"mixer": "recurrent"},
    {"norm": "rms", "ffn": "swiglu", "mixer": "recurrent"},
])
def test_every_variant_passes_gradient_checks(options):
    model = VariantModel(matched(ArchConfig(SMALL, **options)), seed=5)
    x, y = batch(2)
    _, grads = model.loss_and_gradients(x, y)
    for name, param in model.params.items():
        result = check_gradient(lambda: model.loss(x, y), param, grads[name], rtol=1e-4, atol=1e-6)
        assert result.passed, (name, result)


@pytest.mark.parametrize("options", [{}, {"norm": "rms"}, {"ffn": "swiglu"}, {"mixer": "recurrent"}])
def test_no_variant_can_see_future_tokens(options):
    """A model that peeks at later tokens would score a falsely low loss.
    Changing tokens after position t must not change any prediction up to t."""
    rng = np.random.default_rng(0)
    model = VariantModel(matched(ArchConfig(SMALL, **options)), seed=1)
    for param in model.params.values():
        param += rng.normal(0, 0.3, param.shape)            # non-trivial weights, like a trained model
    tokens = rng.integers(0, SMALL.vocab_size, size=SMALL.context_length)
    for t in range(SMALL.context_length - 1):
        changed = tokens.copy()
        changed[t + 1:] = rng.integers(0, SMALL.vocab_size, size=SMALL.context_length - t - 1)
        assert np.array_equal(model(tokens)[: t + 1], model(changed)[: t + 1])


def test_scan_matches_its_definition_and_gradient():
    rng = np.random.default_rng(0)
    u, a = rng.normal(size=(2, 6, 3)), np.array([0.2, 0.5, 0.9])
    h = scan(u, a)
    expected = np.zeros_like(u)
    for t in range(6):
        for s in range(t + 1):
            expected[:, t] += a ** (t - s) * (1 - a) * u[:, s]
    assert np.allclose(h, expected)
    up = rng.normal(size=u.shape)
    du, da = scan_backward(up, u, h, a)
    assert check_gradient(lambda: float((scan(u, a) * up).sum()), u, du).passed
    assert check_gradient(lambda: float((scan(u, a) * up).sum()), a, da).passed


def test_decay_starts_with_memories_from_2_to_32_tokens():
    a = sigmoid(arch.decay_init(16))
    lengths = 1 / (1 - a)
    assert lengths[0] == pytest.approx(2.0) and lengths[-1] == pytest.approx(32.0)
    assert np.all(np.diff(lengths) > 0)


@pytest.mark.parametrize("size", [T0Config(vocab_size=512), T0Config(vocab_size=512, d_model=64, d_ff=256, n_layers=2),
                                  SMALL])
@pytest.mark.parametrize("options", [{"ffn": "swiglu"}, {"mixer": "recurrent"}, {"ffn": "swiglu", "mixer": "recurrent"}])
def test_variants_are_parameter_matched(size, options):
    t0 = AuroraT0(size).num_parameters
    variant = VariantModel(matched(ArchConfig(size, **options))).num_parameters
    assert abs(variant - t0) / t0 < 0.02


def test_config_validation_labels_and_round_trip():
    with pytest.raises(ValueError):
        ArchConfig(SMALL, norm="batch")
    config = ArchConfig(SMALL, norm="rms", mixer="recurrent")
    assert config.label() == "T0 + rms + recurrent" and ArchConfig(SMALL).label() == "T0"
    assert ArchConfig.from_dict(config.to_dict()) == config
    with pytest.raises(ValueError):
        VariantModel(config, params=AuroraT0(SMALL).params)


def test_the_trainer_trains_a_variant():
    rng = np.random.default_rng(0)
    ids = np.tile(np.arange(20), 60) + rng.integers(0, 2, size=1200) * 20     # a learnable pattern
    model = VariantModel(matched(ArchConfig(SMALL, mixer="recurrent")), seed=0)
    data = TokenDataset(ids, 8)
    outcome = train(model, data, data, TrainConfig(steps=60, batch_size=8, eval_interval=30, warmup_steps=5,
                                                   max_lr=1e-2, min_lr=1e-3, eval_windows=20))
    assert outcome.history[-1]["validation_loss"] < math.log(40) - 0.5


# --- Statistics ---------------------------------------------------------------------------------

def test_paired_comparison_by_hand():
    result = stats.paired([0.90, 0.88, 0.92], [1.00, 1.00, 1.00])
    assert result.mean_difference == pytest.approx(-0.1)
    assert result.std_difference == pytest.approx(0.02)
    half = 4.303 * 0.02 / math.sqrt(3)
    assert (result.ci_low, result.ci_high) == (pytest.approx(-0.1 - half), pytest.approx(-0.1 + half))
    assert result.decision == stats.BETTER and result.wins == 3


def test_decisions():
    assert stats.paired([1.1, 1.12, 1.09], [1.0, 1.0, 1.0]).decision == stats.WORSE
    assert stats.paired([0.9, 1.1, 1.0], [1.0, 1.0, 1.0]).decision == stats.SAME
    tiny = stats.paired([0.995, 0.994, 0.996], [1.0, 1.0, 1.0], min_effect=0.01)
    assert tiny.ci_high < 0 and tiny.decision == stats.SAME          # real but too small to claim
    with pytest.raises(ValueError):
        stats.paired([1.0], [1.0, 2.0])
    assert stats.t_critical(4) == 2.776 and stats.t_critical(100) == 1.96
    summary = stats.summary([1.0, 2.0, 3.0])
    assert summary["mean"] == 2.0 and summary["min"] == 1.0 and summary["ci_low"] < 2.0 < summary["ci_high"]


# --- Runner ---------------------------------------------------------------------------------------

WORDS = ("the of and a to in is was that he it his with as for had you not be her on at by which have or "
         "from this him but all she they were my are me one their so an said them we who would been will no").split()


@pytest.fixture
def text_file(tmp_path):
    rng = np.random.default_rng(0)
    paragraphs = [" ".join(rng.choice(WORDS, size=60)) + "." for _ in range(160)]
    path = tmp_path / "book.txt"
    path.write_text("\n\n".join(paragraphs), encoding="utf-8")
    return path


def tiny_spec(path, **changes):
    spec = {"name": "tiny", "question": "Does it work?", "hypothesis": "Yes.",
            "data": {"file": path.name, "sha256": runner.sha256_file(path)},
            "tokenizer": {"vocab_size": 300}, "model": {"context_length": 8, "d_model": 8, "n_heads": 2, "d_ff": 16},
            "steps": 6, "tokens_per_step": 64, "eval_interval": 3, "test_windows": 20,
            "train": {"warmup_steps": 1, "eval_windows": 10}, "seeds": [0, 1, 2], "baseline": "T0",
            "variants": [{"name": "T0"}, {"name": "Recurrent", "mixer": "recurrent"}],
            "decision": {"metric": "best_validation_loss", "min_effect": 0.0},
            "limitations": ["Tiny test experiment."]}
    spec.update(changes)
    return spec


def write_spec(tmp_path, spec):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_spec_validation(tmp_path, text_file):
    base = tiny_spec(text_file)
    with pytest.raises(runner.SpecError, match="missing: decision"):
        runner.load_spec(write_spec(tmp_path, {k: v for k, v in base.items() if k != "decision"}))
    with pytest.raises(runner.SpecError, match="at least 3 seeds"):
        runner.load_spec(write_spec(tmp_path, {**base, "seeds": [0, 1]}))
    with pytest.raises(runner.SpecError, match="include the baseline"):
        runner.load_spec(write_spec(tmp_path, {**base, "baseline": "Missing"}))
    with pytest.raises(ValueError):
        runner.load_spec(write_spec(tmp_path, {**base, "variants": [{"name": "T0"}, {"name": "X", "mixer": "lstm"}]}))
    assert runner.load_spec(write_spec(tmp_path, base))["name"] == "tiny"


def test_data_must_match_its_fingerprint(tmp_path, text_file):
    spec = tiny_spec(text_file)
    spec["data"]["sha256"] = "0" * 64
    with pytest.raises(runner.SpecError, match="isn't the file this experiment was specified for"):
        runner.prepare(spec, text_file)


def test_fingerprints(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_bytes(b"x = 1\n")
    monkeypatch.setattr(runner, "CORE", tmp_path)
    first = runner.code_fingerprint()
    (tmp_path / "a.py").write_bytes(b"x = 1\r\n")               # line endings don't matter
    assert runner.code_fingerprint() == first
    (tmp_path / "a.py").write_bytes(b"x = 2\n")
    assert runner.code_fingerprint() != first
    params = {"w": np.zeros(3, dtype=np.float32)}
    assert runner.weights_fingerprint(params) == runner.weights_fingerprint({"w": np.zeros(3, dtype=np.float32)})
    assert runner.weights_fingerprint(params) != runner.weights_fingerprint({"w": np.ones(3, dtype=np.float32)})


def test_experiment_runs_resumes_and_verifies(tmp_path, text_file, monkeypatch):
    spec = runner.load_spec(write_spec(tmp_path, tiny_spec(text_file)))
    out = tmp_path / "results"
    experiment = runner.Experiment(spec, text_file, out)
    experiment.run(limit=3)
    assert [r["id"] for r in experiment.recorded()] == ["T0 | seed 0", "Recurrent | seed 0", "T0 | seed 1"]
    calls = []
    original = runner.train_one
    monkeypatch.setattr(runner, "train_one", lambda *a: calls.append(a[2]["name"]) or original(*a))
    experiment.run()
    assert len(calls) == 3 and len(experiment.recorded()) == 6        # only the missing runs
    record = experiment.recorded()[0]
    assert len(record["weights_sha256"]) == 64 and record["data_sha256"] == spec["data"]["sha256"]
    assert record["architecture"]["parameters"] > 0 and record["history"]
    check = experiment.verify("Recurrent | seed 1")
    assert check["identical_weights"] and check["code_matches"]
    with pytest.raises(runner.SpecError, match="no recorded run"):
        experiment.verify("Nothing | seed 9")
    analysis = runner.write_outputs(experiment)
    assert analysis["complete"] and analysis["variants"][1]["validation_comparison"]["n"] == 3
    assert "baseline_vs_bigram" in analysis
    report = (out / "report.md").read_text(encoding="utf-8")
    for expected in ("Decision rule (written before running)", "Hypothesis (written before running)",
                     "Reproduce:", "Re-runs from scratch", "Tiny test experiment.", "seed 2"):
        assert expected in report
    assert json.loads((out / "environment.json").read_text())["code_fingerprint"] == runner.code_fingerprint()


def test_verification_detects_different_weights(tmp_path, text_file, capsys):
    from aurora_core.research.__main__ import main

    spec_path = write_spec(tmp_path, tiny_spec(text_file))
    out = tmp_path / "results"
    experiment = runner.Experiment(runner.load_spec(spec_path), text_file, out)
    experiment.run(limit=1)
    lines = experiment.runs_file.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["weights_sha256"] = "f" * 64                      # as if the recorded result had been edited
    experiment.runs_file.write_text(json.dumps(record) + "\n", encoding="utf-8")
    check = experiment.verify("T0 | seed 0")
    assert not check["identical_weights"]
    assert main(["verify", "--spec", str(spec_path), "--data", str(text_file), "--out", str(out),
                 "--run", "T0 | seed 0"]) == 1
    assert r"| T0 \| seed 0 | NO |" in (out / "report.md").read_text(encoding="utf-8")


def test_only_seeds_both_sides_ran_are_compared():
    spec = {"name": "x", "question": "?", "baseline": "T0", "seeds": [0, 1, 2], "decision": {"min_effect": 0.0},
            "variants": [{"name": "T0"}, {"name": "V"}]}
    runs = [run_record("T0", 0, 3.6, 3.6), run_record("T0", 1, 3.7, 3.7),
            run_record("V", 0, 3.5, 3.5), run_record("V", 1, 3.6, 3.6), run_record("V", 2, 9.9, 9.9)]
    variant = runner.analyse(spec, runs)["variants"][1]
    assert variant["seeds"] == [0, 1] and variant["validation_comparison"]["n"] == 2
    assert variant["validation_comparison"]["mean_difference"] == pytest.approx(-0.1)


def test_runs_with_other_code_are_not_mixed(tmp_path, text_file, monkeypatch):
    spec = runner.load_spec(write_spec(tmp_path, tiny_spec(text_file)))
    experiment = runner.Experiment(spec, text_file, tmp_path / "results")
    experiment.run(limit=1)
    monkeypatch.setattr(runner, "code_fingerprint", lambda: "different")
    with pytest.raises(runner.SpecError, match="different code or data"):
        experiment.run()
    experiment.run(fresh=True, limit=1)
    assert len(experiment.recorded()) == 1 and experiment.recorded()[0]["code_fingerprint"] == "different"


def run_record(variant, seed, validation, test):
    return {"id": f"{variant} | seed {seed}", "variant": variant, "seed": seed, "best_validation_loss": validation,
            "test_loss": test, "seconds": 1.0, "architecture": {"parameters": 100, "label": variant}}


def test_analysis_verdicts():
    spec = {"name": "x", "question": "?", "baseline": "T0", "seeds": [0, 1, 2], "decision": {"min_effect": 0.01},
            "variants": [{"name": "T0"}, {"name": "Good"}, {"name": "Bad"}, {"name": "Lucky"}]}
    runs = []
    for seed, base in enumerate([3.60, 3.62, 3.58]):
        runs += [run_record("T0", seed, base, base),
                 run_record("Good", seed, base - 0.05 - 0.002 * seed, base - 0.04),
                 run_record("Bad", seed, base + 0.05 + 0.003 * seed, base + 0.05),
                 run_record("Lucky", seed, base - 0.05 - 0.002 * seed, base + 0.02 + 0.001 * seed)]
    verdicts = {v["name"]: v.get("verdict") for v in runner.analyse(spec, runs)["variants"]}
    assert verdicts == {"T0": None, "Good": "improvement (confirmed on test)", "Bad": "worse",
                        "Lucky": "improvement on validation, not confirmed on test"}
    incomplete = runner.analyse(spec, runs[:-4])
    assert not incomplete["complete"]


def test_claims_register(tmp_path):
    folder = tmp_path / "exp"
    folder.mkdir()
    spec = {"name": "exp", "question": "?", "baseline": "T0", "seeds": [0, 1, 2], "decision": {"min_effect": 0.0},
            "variants": [{"name": "T0"}, {"name": "Good"}]}
    runs = []
    for seed in range(3):
        runs += [run_record("T0", seed, 3.6 + seed / 100, 3.6), run_record("Good", seed, 3.5 + seed / 100 + seed / 1000, 3.5)]
    (folder / "results.json").write_text(json.dumps(runner.analyse(spec, runs)))
    text = claims.write_claims(tmp_path).read_text(encoding="utf-8")
    assert "| exp | Good | T0 | -0.099 [-0.101, -0.097]" in text and "improvement (confirmed on test)" in text
    assert "[report](exp/report.md)" in text
    empty = tmp_path / "empty"
    assert "No experiments recorded yet" in claims.write_claims(empty).read_text(encoding="utf-8")


def test_command_line(tmp_path, text_file, capsys):
    from aurora_core.research.__main__ import main

    spec_path = write_spec(tmp_path, tiny_spec(text_file, seeds=[0, 1, 2], variants=[{"name": "T0"}, {"name": "RMS", "norm": "rms"}]))
    out = tmp_path / "out"
    assert main(["run", "--spec", str(spec_path), "--data", str(text_file), "--out", str(out)]) == 0
    assert "RMS" in capsys.readouterr().out
    assert main(["verify", "--spec", str(spec_path), "--data", str(text_file), "--out", str(out), "--run", "RMS | seed 2"]) == 0
    assert main(["report", "--spec", str(spec_path), "--data", str(text_file), "--out", str(out)]) == 0
    bad = write_spec(tmp_path, {**tiny_spec(text_file), "seeds": [0]})
    assert main(["run", "--spec", str(bad), "--data", str(text_file), "--out", str(out)]) == 2
    assert main(["claims", "--root", str(tmp_path)]) == 0


def test_the_architecture_spec_is_valid():
    spec = runner.load_spec(runner.CORE / "research" / "specs" / "architecture_v1.json")
    assert len(spec["seeds"]) >= 5 and spec["data"]["sha256"] and spec["hypothesis"] and spec["limitations"]
    assert {v["name"] for v in spec["variants"]} == {"T0", "RMSNorm", "SwiGLU", "Recurrent"}
