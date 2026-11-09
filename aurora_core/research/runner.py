"""
Reproducible experiments: specification -> runs -> analysis -> report.

An experiment is a JSON specification (aurora_core/research/specs/),
written before anything is run, stating the question, the data (with its
SHA-256), the baseline, the variants (one change each), the seeds and the
decision rule. Running it:

1. checks the data file is byte-for-byte the one the specification names;
2. records the environment: Python, NumPy, CPU, operating system, the git
   commit and a fingerprint of every aurora_core source file;
3. trains the baseline and every variant once per seed. A variant and the
   baseline with the same seed see the same batches in the same order;
   variants are parameter-matched to the baseline;
4. saves each run as it finishes (runs.jsonl): its configuration, loss
   history, best validation loss, test loss and a SHA-256 of its final
   weights. An interrupted experiment resumes where it stopped, but only
   with the same code and data (otherwise results wouldn't be comparable);
5. analyses the runs with the decision rule (stats.py): validation loss
   decides; the test text is used only to confirm a claimed improvement;
6. writes results.json and report.md.

``verify`` re-trains one run from scratch and checks that its weights
are identical (same SHA-256) to the recorded ones: the evidence can be
reproduced, not just re-read.
"""

import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np

from aurora_core.research import stats
from aurora_core.research.arch import ArchConfig, VariantModel, describe, matched
from aurora_core.tokenizer import ByteBPETokenizer
from aurora_core.training.baselines import bigram_loss
from aurora_core.training.data import TokenDataset, read_text, split_text, strip_gutenberg
from aurora_core.training.trainer import TrainConfig, evaluate, train
from aurora_core.transformer import T0Config

CORE = Path(__file__).resolve().parents[1]           # aurora_core/
REPOSITORY = CORE.parent


class SpecError(ValueError):
    """The specification or the data doesn't match what it promises."""


# --- Specification --------------------------------------------------------------------------

REQUIRED = ("name", "question", "data", "steps", "seeds", "variants", "decision")


def load_spec(path: str | Path) -> dict:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED if key not in spec]
    if missing:
        raise SpecError(f"specification is missing: {', '.join(missing)}")
    if len(spec["seeds"]) < 3:
        raise SpecError("use at least 3 seeds: with fewer, chance can't be told apart from an effect")
    names = [variant["name"] for variant in spec["variants"]]
    if len(set(names)) != len(names) or spec.get("baseline", "T0") not in names:
        raise SpecError("variant names must be unique and include the baseline")
    for variant in spec["variants"]:
        arch_of(spec, variant)                          # validates options
    return spec


def arch_of(spec: dict, variant: dict) -> ArchConfig:
    base = T0Config(**{"vocab_size": spec.get("tokenizer", {}).get("vocab_size", 512), **spec.get("model", {})})
    arch = ArchConfig(base, variant.get("norm", "layer"), variant.get("ffn", "gelu"), variant.get("mixer", "attention"))
    return matched(arch) if variant.get("match_parameters", True) and not arch.is_t0 else arch


# --- Fingerprints -------------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_fingerprint() -> str:
    """SHA-256 over every aurora_core source file (path and contents)."""
    digest = hashlib.sha256()
    for path in sorted(CORE.rglob("*.py")):
        digest.update(path.relative_to(CORE).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def weights_fingerprint(params: dict[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for name in sorted(params):
        digest.update(name.encode())
        digest.update(np.ascontiguousarray(params[name]).tobytes())
    return digest.hexdigest()


def git_state() -> dict:
    def git(*args):
        result = subprocess.run(["git", "-c", f"safe.directory={REPOSITORY.as_posix()}", *args], cwd=REPOSITORY,
                                capture_output=True, text=True, timeout=20)
        return result.stdout.strip() if result.returncode == 0 else ""
    try:
        commit = git("rev-parse", "HEAD")
        dirty = bool(git("status", "--porcelain", "--", "aurora_core"))
    except (OSError, subprocess.SubprocessError):
        commit, dirty = "", None
    return {"commit": commit or "unknown", "aurora_core_uncommitted_changes": dirty}


def environment() -> dict:
    return {
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "code_fingerprint": code_fingerprint(),
        **git_state(),
    }


# --- Data ---------------------------------------------------------------------------------------

def prepare(spec: dict, data_path: Path) -> dict:
    digest = sha256_file(data_path)
    expected = spec["data"].get("sha256")
    if expected and expected != digest:
        raise SpecError(f"{data_path.name} isn't the file this experiment was specified for "
                        f"(SHA-256 {digest[:12]}…, expected {expected[:12]}…).")
    text = strip_gutenberg(read_text(data_path))
    split = spec.get("splits", {})
    splits = split_text(text, split.get("validation", 0.05), split.get("test", 0.05))
    tokenizer = ByteBPETokenizer.train([splits.train], vocab_size=spec.get("tokenizer", {}).get("vocab_size", 512))
    tokenizer_json = tokenizer.to_json()
    ids = {part: np.array(tokenizer.encode(getattr(splits, part)), dtype=np.int64)
           for part in ("train", "validation", "test")}
    return {"ids": ids, "vocab_size": tokenizer.vocab_size, "data_sha256": digest,
            "tokenizer_sha256": hashlib.sha256(tokenizer_json.encode()).hexdigest(),
            "tokens": {part: int(len(value)) for part, value in ids.items()}}


# --- Runs ---------------------------------------------------------------------------------------

def run_id(variant: str, seed: int) -> str:
    return f"{variant} | seed {seed}"


def train_one(spec: dict, data: dict, variant: dict, seed: int) -> dict:
    arch = arch_of(spec, variant)
    arch = replace(arch, model=replace(arch.model, vocab_size=data["vocab_size"]))
    tokens_per_step = spec.get("tokens_per_step", 1024)
    batch = max(1, tokens_per_step // arch.model.context_length)
    config = TrainConfig(**{**spec.get("train", {}), "steps": spec["steps"], "batch_size": batch, "seed": seed,
                            "eval_interval": spec.get("eval_interval", max(1, spec["steps"] // 8))})
    train_data = TokenDataset(data["ids"]["train"], arch.model.context_length)
    validation = TokenDataset(data["ids"]["validation"], arch.model.context_length)
    test = TokenDataset(data["ids"]["test"], arch.model.context_length)
    model = VariantModel(arch, seed=seed)
    outcome = train(model, train_data, validation, config)
    model.params = outcome.best_params
    test_loss = evaluate(model, test, spec.get("test_windows"))
    return {
        "id": run_id(variant["name"], seed), "variant": variant["name"], "seed": seed,
        "architecture": describe(arch), "train_config": config.to_dict(),
        "best_validation_loss": outcome.best_validation_loss, "best_step": outcome.best_step,
        "test_loss": test_loss, "history": outcome.history, "seconds": round(outcome.seconds, 1),
        "weights_sha256": weights_fingerprint(outcome.best_params),
    }


class Experiment:

    def __init__(self, spec: dict, data_path: Path, out: Path):
        self.spec, self.data_path, self.out = spec, Path(data_path), Path(out)
        self._data = None

    @property
    def data(self) -> dict:
        if self._data is None:
            self._data = prepare(self.spec, self.data_path)
        return self._data

    @property
    def runs_file(self) -> Path:
        return self.out / "runs.jsonl"

    def recorded(self) -> list[dict]:
        if not self.runs_file.is_file():
            return []
        return [json.loads(line) for line in self.runs_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def plan(self) -> list[tuple[dict, int]]:
        """Every (variant, seed), seeds outermost, so a stopped experiment still
        has complete pairs for the seeds it finished."""
        return [(variant, seed) for seed in self.spec["seeds"] for variant in self.spec["variants"]]

    def run(self, fresh: bool = False, on_run=None, limit: int | None = None) -> list[dict]:
        self.out.mkdir(parents=True, exist_ok=True)
        env = environment()
        data = self.data
        identity = {"code_fingerprint": env["code_fingerprint"], "data_sha256": data["data_sha256"],
                    "tokenizer_sha256": data["tokenizer_sha256"]}
        done = {} if fresh else {run["id"]: run for run in self.recorded()}
        stale = [run["id"] for run in done.values() if any(run.get(k) != v for k, v in identity.items())]
        if stale:
            raise SpecError(f"{len(stale)} recorded runs were made with different code or data; "
                            "run again with --fresh to start over (results can't be mixed).")
        if fresh and self.runs_file.exists():
            self.runs_file.unlink()
        (self.out / "spec.json").write_text(json.dumps(self.spec, indent=2), encoding="utf-8")
        (self.out / "environment.json").write_text(json.dumps({**env, **{k: v for k, v in data.items() if k != "ids"}},
                                                              indent=2), encoding="utf-8")
        finished = 0
        for variant, seed in self.plan():
            key = run_id(variant["name"], seed)
            if key in done:
                continue
            if limit is not None and finished >= limit:
                break
            record = {**train_one(self.spec, data, variant, seed), **identity,
                      "finished": datetime.now().isoformat(timespec="seconds")}
            with self.runs_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            done[key] = record
            finished += 1
            if on_run:
                on_run(record)
        return list(done.values())

    def verify(self, key: str) -> dict:
        """Re-trains one recorded run and compares its weights and losses."""
        recorded = {run["id"]: run for run in self.recorded()}
        if key not in recorded:
            raise SpecError(f"no recorded run called {key!r}")
        old = recorded[key]
        variant = next(v for v in self.spec["variants"] if v["name"] == old["variant"])
        started = time.perf_counter()
        new = train_one(self.spec, self.data, variant, old["seed"])
        same = new["weights_sha256"] == old["weights_sha256"]
        report = {"id": key, "identical_weights": same,
                  "recorded": {k: old[k] for k in ("best_validation_loss", "test_loss", "weights_sha256")},
                  "reproduced": {k: new[k] for k in ("best_validation_loss", "test_loss", "weights_sha256")},
                  "code_matches": old.get("code_fingerprint") == code_fingerprint(),
                  "seconds": round(time.perf_counter() - started, 1),
                  "time": datetime.now().isoformat(timespec="seconds")}
        verifications = self.out / "verifications.jsonl"
        with verifications.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(report) + "\n")
        return report


# --- Analysis -----------------------------------------------------------------------------------

def analyse(spec: dict, runs: list[dict], data: dict | None = None) -> dict:
    baseline = spec.get("baseline", "T0")
    rule = spec["decision"]
    min_effect = float(rule.get("min_effect", 0.0))
    by = {}
    for run in runs:
        by.setdefault(run["variant"], {})[run["seed"]] = run
    base_runs = by.get(baseline, {})
    variants = []
    for variant in spec["variants"]:
        mine = by.get(variant["name"], {})
        seeds = sorted(set(mine) & set(base_runs))
        entry = {"name": variant["name"], "question": variant.get("question", ""),
                 "architecture": next(iter(mine.values()))["architecture"] if mine else None,
                 "seeds": seeds, "validation": stats.summary([mine[s]["best_validation_loss"] for s in seeds]) if seeds else None,
                 "test": stats.summary([mine[s]["test_loss"] for s in seeds]) if seeds else None,
                 "seconds": stats.summary([mine[s]["seconds"] for s in seeds]) if seeds else None}
        if variant["name"] != baseline and len(seeds) >= 2:
            validation = stats.paired([mine[s]["best_validation_loss"] for s in seeds],
                                      [base_runs[s]["best_validation_loss"] for s in seeds], min_effect)
            entry["validation_comparison"] = validation.to_dict()
            confirmation = None
            if validation.decision == stats.BETTER:
                confirmation = stats.paired([mine[s]["test_loss"] for s in seeds],
                                            [base_runs[s]["test_loss"] for s in seeds], 0.0).to_dict()
            entry["test_confirmation"] = confirmation
            confirmed = validation.decision == stats.BETTER and confirmation and confirmation["decision"] == stats.BETTER
            entry["verdict"] = ("improvement (confirmed on test)" if confirmed else
                                "improvement on validation, not confirmed on test" if validation.decision == stats.BETTER else
                                "worse" if validation.decision == stats.WORSE else stats.SAME)
        variants.append(entry)
    result = {"name": spec["name"], "question": spec["question"], "baseline": baseline, "decision_rule": rule,
              "complete": all(len(v["seeds"]) == len(spec["seeds"]) for v in variants), "variants": variants}
    if data is not None and len(base_runs) >= 2:          # a confidence interval needs two runs
        context = spec.get("model", {}).get("context_length", T0Config().context_length)
        inputs, targets = TokenDataset(data["ids"]["test"], context).windows(spec.get("test_windows"))
        bigram = bigram_loss(data["ids"]["train"], inputs, targets, data["vocab_size"])
        seeds = sorted(base_runs)
        result["baseline_vs_bigram"] = {
            "bigram_test_loss": bigram,
            "comparison": stats.against_constant([base_runs[s]["test_loss"] for s in seeds], bigram).to_dict()}
    return result


# --- Report -------------------------------------------------------------------------------------

def _fmt(value: float, digits: int = 3) -> str:
    return "—" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{value:.{digits}f}"


def report(spec: dict, analysis: dict, env: dict, runs: list[dict], verifications: list[dict]) -> str:
    rule = analysis["decision_rule"]
    lines = [f"# Experiment: {spec['name']}", "", f"**Question.** {spec['question']}", ""]
    if spec.get("hypothesis"):
        lines += [f"**Hypothesis (written before running).** {spec['hypothesis']}", ""]
    lines += [
        "**Decision rule (written before running).** A variant is an improvement only if the 95 % confidence "
        f"interval of its mean paired difference in best validation loss against `{analysis['baseline']}` lies "
        f"entirely below zero and the mean improvement is at least {rule.get('min_effect', 0)} nats; the test text "
        "is then used once to confirm it. Otherwise the result is reported as no detectable difference (or worse).",
        "",
        f"**Status.** {'Complete' if analysis['complete'] else 'Incomplete'}: {len(runs)} runs, seeds {spec['seeds']}, "
        f"{spec['steps']} steps of {spec.get('tokens_per_step', 1024)} tokens each.",
        "",
        "## Results",
        "",
        "| Variant | Parameters | Best validation loss (mean ± sd) | Difference vs baseline [95 % CI] | Seeds better | Test loss (mean) | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for variant in analysis["variants"]:
        if not variant["seeds"]:
            continue
        v = variant["validation"]
        comparison = variant.get("validation_comparison")
        diff = ("—" if comparison is None else
                f"{comparison['mean_difference']:+.3f} [{comparison['ci_low']:+.3f}, {comparison['ci_high']:+.3f}]")
        wins = "—" if comparison is None else f"{comparison['wins']} of {comparison['n']}"
        lines.append(f"| {variant['name']} | {variant['architecture']['parameters']:,} | {_fmt(v['mean'])} ± {_fmt(v['std'])} | "
                     f"{diff} | {wins} | {_fmt(variant['test']['mean'])} | {variant.get('verdict', 'baseline')} |")
    if "baseline_vs_bigram" in analysis:
        comparison = analysis["baseline_vs_bigram"]["comparison"]
        lines += ["", f"The baseline's test loss is {comparison['mean_difference']:+.3f} nats from the bigram baseline "
                      f"({analysis['baseline_vs_bigram']['bigram_test_loss']:.3f}) [95 % CI {comparison['ci_low']:+.3f}, "
                      f"{comparison['ci_high']:+.3f}]: {'it beats the bigram baseline' if comparison['decision'] == stats.BETTER else 'no detectable difference from the bigram baseline' if comparison['decision'] == stats.SAME else 'worse than the bigram baseline'}."]
    lines += ["", "### Per seed (best validation loss)", "", "| Variant | " + " | ".join(f"seed {s}" for s in spec["seeds"]) + " |",
              "|---|" + "---|" * len(spec["seeds"])]
    by = {(run["variant"], run["seed"]): run for run in runs}
    for variant in spec["variants"]:
        cells = [_fmt(by[(variant["name"], s)]["best_validation_loss"]) if (variant["name"], s) in by else "—"
                 for s in spec["seeds"]]
        lines.append(f"| {variant['name']} | " + " | ".join(cells) + " |")
    lines += ["", "## Reproducibility", "",
              f"- Data: `{spec['data']['file']}` SHA-256 `{env.get('data_sha256', '?')}`"
              + (f" ({spec['data']['source']})" if spec['data'].get("source") else ""),
              f"- Tokenizer: byte-level BPE, {env.get('vocab_size', '?')} tokens, trained on the training part only; "
              f"SHA-256 `{env.get('tokenizer_sha256', '?')}`",
              f"- Tokens: {env.get('tokens', {})}",
              f"- Code: aurora_core fingerprint `{env.get('code_fingerprint', '?')[:16]}…`, git commit "
              f"`{env.get('commit', '?')[:12]}`" + (" (with uncommitted changes)" if env.get("aurora_core_uncommitted_changes") else ""),
              f"- Environment: Python {env.get('python')}, NumPy {env.get('numpy')}, {env.get('platform')}, "
              f"{env.get('processor')}",
              "- Every run's configuration, loss history and a SHA-256 of its final weights are in `runs.jsonl`.",
              "", "Reproduce:", "", "```",
              f"python -m aurora_core.research run --spec aurora_core/research/specs/{spec['name']}.json --data {spec['data']['file']}",
              f"python -m aurora_core.research verify --spec aurora_core/research/specs/{spec['name']}.json "
              f"--data {spec['data']['file']} --run \"{analysis['baseline']} | seed {spec['seeds'][0]}\"",
              "```"]
    if verifications:
        lines += ["", "### Re-runs from scratch", "", "| Run | Identical weights | Recorded validation loss | Reproduced |", "|---|---|---|---|"]
        for check in verifications:
            lines.append(f"| {check['id']} | {'yes' if check['identical_weights'] else 'NO'} | "
                         f"{check['recorded']['best_validation_loss']:.6f} | {check['reproduced']['best_validation_loss']:.6f} |")
    if spec.get("limitations"):
        lines += ["", "## Limitations", ""] + [f"- {item}" for item in spec["limitations"]]
    return "\n".join(lines) + "\n"


def write_outputs(experiment: Experiment) -> dict:
    runs = experiment.recorded()
    env_path = experiment.out / "environment.json"
    env = json.loads(env_path.read_text(encoding="utf-8")) if env_path.is_file() else {}
    analysis = analyse(experiment.spec, runs, experiment.data)
    analysis["environment"] = env
    (experiment.out / "results.json").write_text(json.dumps(analysis, indent=2), encoding="utf-8")
    verifications_path = experiment.out / "verifications.jsonl"
    verifications = ([json.loads(line) for line in verifications_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                     if verifications_path.is_file() else [])
    (experiment.out / "report.md").write_text(report(experiment.spec, analysis, env, runs, verifications),
                                              encoding="utf-8")
    return analysis
