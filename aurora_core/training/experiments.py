"""
Controlled experiments: train several variants of AURORA-T0 on the same
data and compare their validation loss.

    .venv\\Scripts\\python.exe -m aurora_core.training.experiments --data Data\\pg1661.txt

Every variant uses the same text, the same tokenizer (trained once, on
the training part), the same number of steps and the same number of
tokens per step (batch size x context length), so differences come from
the one setting that was changed. Results are compared on validation
loss only; the test text is not used here.

Each variant is one change from the T0 baseline:
    context length 16 / 64
    d_model 64, 2 layers, 4 heads
    initialisation std 0.1 (5x larger)
    learning rate 1e-3 / 1e-2
    gradient clipping 0.5 / 5.0
    no weight decay
    baseline with seeds 1 and 2 (to measure run-to-run variation)
"""

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from aurora_core.tokenizer import ByteBPETokenizer
from aurora_core.training.data import TokenDataset, read_text, split_text, strip_gutenberg
from aurora_core.training.trainer import TrainConfig, train
from aurora_core.transformer import AuroraT0, T0Config

TOKENS_PER_STEP = 1024

EXPERIMENTS: list[dict] = [
    {"name": "T0 baseline"},
    {"name": "context 16", "model": {"context_length": 16}},
    {"name": "context 64", "model": {"context_length": 64}},
    {"name": "d_model 64", "model": {"d_model": 64, "d_ff": 256}},
    {"name": "2 layers", "model": {"n_layers": 2}},
    {"name": "4 heads", "model": {"n_heads": 4}},
    {"name": "init std 0.1", "model": {"init_std": 0.1}},
    {"name": "learning rate 1e-3", "train": {"max_lr": 1e-3, "min_lr": 1e-4}},
    {"name": "learning rate 1e-2", "train": {"max_lr": 1e-2, "min_lr": 1e-3}},
    {"name": "grad clip 0.5", "train": {"grad_clip": 0.5}},
    {"name": "grad clip 5.0", "train": {"grad_clip": 5.0}},
    {"name": "no weight decay", "train": {"weight_decay": 0.0}},
    # The baseline again with other random seeds (different initial weights
    # and batches): how much results vary by chance alone.
    {"name": "T0 baseline, seed 1", "train": {"seed": 1}},
    {"name": "T0 baseline, seed 2", "train": {"seed": 2}},
]


def run_experiments(train_ids, val_ids, vocab_size: int, experiments: list[dict], steps: int,
                    base_model: T0Config | None = None, base_train: TrainConfig | None = None,
                    tokens_per_step: int = TOKENS_PER_STEP, on_result=None) -> list[dict]:
    base_model = base_model or T0Config(vocab_size=vocab_size)
    base_train = base_train or TrainConfig(steps=steps, eval_interval=max(1, steps // 4))
    results = []
    for experiment in experiments:
        model_config = replace(base_model, **experiment.get("model", {}))
        batch = max(1, tokens_per_step // model_config.context_length)
        train_config = replace(base_train, batch_size=batch, **experiment.get("train", {}))
        train_data = TokenDataset(train_ids, model_config.context_length)
        val_data = TokenDataset(val_ids, model_config.context_length)
        model = AuroraT0(model_config, seed=train_config.seed)
        outcome = train(model, train_data, val_data, train_config)
        last = outcome.history[-1]
        result = {
            "name": experiment["name"],
            "changes": {**experiment.get("model", {}), **experiment.get("train", {})},
            "parameters": model.num_parameters,
            "batch_size": batch,
            "best_validation_loss": outcome.best_validation_loss,
            "final_train_loss": last["train_loss"],
            "overfit_gap": last["validation_loss"] - last["train_loss"],
            "final_grad_norm": last["grad_norm"],
            "seconds": round(outcome.seconds, 1),
            "history": outcome.history,
        }
        results.append(result)
        if on_result:
            on_result(result)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core.training.experiments")
    parser.add_argument("--data", nargs="+", required=True)
    parser.add_argument("--out", default="Models/experiments/results.json")
    parser.add_argument("--steps", type=int, default=2000)
    parser.add_argument("--vocab-size", type=int, default=512)
    parser.add_argument("--only", nargs="*", help="names of experiments to run (default: all)")
    args = parser.parse_args(argv)

    text = "\n".join(strip_gutenberg(read_text(p)) for p in args.data)
    splits = split_text(text)
    tokenizer = ByteBPETokenizer.train([splits.train], vocab_size=args.vocab_size)
    train_ids = np.array(tokenizer.encode(splits.train), dtype=np.int64)
    val_ids = np.array(tokenizer.encode(splits.validation), dtype=np.int64)
    chosen = [e for e in EXPERIMENTS if not args.only or e["name"] in args.only]

    # Results are saved after every experiment. If a previous run with the
    # same settings was interrupted, its finished experiments are kept and
    # only the missing ones are run.
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    settings = {"steps": args.steps, "tokens_per_step": TOKENS_PER_STEP,
                "vocab_size": tokenizer.vocab_size, "data": [Path(p).name for p in args.data],
                "base_model": T0Config(vocab_size=tokenizer.vocab_size).to_dict(),
                "base_train": TrainConfig(steps=args.steps, eval_interval=max(1, args.steps // 4)).to_dict()}
    done: dict[str, dict] = {}
    if out.is_file():
        try:
            previous = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}
        if all(previous.get(k) == v for k, v in settings.items()):
            done = {r["name"]: r for r in previous.get("results", [])}

    def save():
        ordered = [done[e["name"]] for e in EXPERIMENTS if e["name"] in done]
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps({**settings, "results": ordered}, indent=2), encoding="utf-8")
        tmp.replace(out)

    def show(r):
        print(f"{r['name']:22}{r['parameters']:9,}{r['best_validation_loss']:10.3f}"
              f"{r['final_train_loss']:8.3f}{r['overfit_gap']:7.3f}{r['final_grad_norm']:7.2f}"
              f"{r['seconds']:7.0f} s", flush=True)

    def finished(r):
        done[r["name"]] = r
        save()
        show(r)

    todo = [e for e in chosen if e["name"] not in done]
    print(f"{len(chosen)} experiments x {args.steps} steps x {TOKENS_PER_STEP} tokens"
          + (f" ({len(chosen) - len(todo)} already done, kept)" if len(todo) < len(chosen) else "") + "\n")
    print(f"{'experiment':22}{'params':>9}{'val loss':>10}{'train':>8}{'gap':>7}{'|g|':>7}{'time':>8}")
    for e in chosen:
        if e["name"] in done:
            show(done[e["name"]])
    run_experiments(train_ids, val_ids, tokenizer.vocab_size, todo, args.steps, on_result=finished)
    results = [done[e["name"]] for e in chosen]
    best = min(results, key=lambda r: r["best_validation_loss"])
    print(f"\nBest: {best['name']} ({best['best_validation_loss']:.3f}, "
          f"perplexity {math.exp(best['best_validation_loss']):.1f}). Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
