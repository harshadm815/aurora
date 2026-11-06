"""
Train AURORA-T0 on a text file:

    .venv\\Scripts\\python.exe -m aurora_core.training --data Data\\pg1661.txt

Steps:
 1. Read the text (Project Gutenberg licence header/footer removed).
 2. Split it 90 / 5 / 5 into training, validation and test text.
 3. Train the BPE tokenizer on the training text only.
 4. Measure the baselines (uniform, unigram, bigram) on validation and test.
 5. Train, evaluating on validation every --eval-interval steps.
 6. Evaluate the best model (by validation loss) once on the test text.
 7. Save to --out: best.npz, last.npz (checkpoints, each containing the
    tokenizer), tokenizer.json, metrics.jsonl and summary.json.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from aurora_core.tokenizer import ByteBPETokenizer
from aurora_core.training import (
    TokenDataset,
    TrainConfig,
    bigram_loss,
    evaluate,
    read_text,
    save_checkpoint,
    split_text,
    strip_gutenberg,
    train,
    unigram_loss,
    uniform_loss,
)
from aurora_core.transformer import AuroraT0, T0Config


def baselines(train_ids, dataset: TokenDataset, vocab: int) -> dict:
    inputs, targets = dataset.windows()
    return {
        "uniform": uniform_loss(vocab),
        "unigram": unigram_loss(train_ids, targets, vocab),
        "bigram": bigram_loss(train_ids, inputs, targets, vocab),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core.training", description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", nargs="+", required=True, help="UTF-8 text file(s)")
    parser.add_argument("--out", default="Models/aurora_t0", help="output folder")
    parser.add_argument("--vocab-size", type=int, default=512)
    parser.add_argument("--steps", type=int, default=3000)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-2,
                        help="peak learning rate (1e-2 was best in the Phase 5 experiments)")
    parser.add_argument("--eval-interval", type=int, default=250)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    text = "\n".join(strip_gutenberg(read_text(path)) for path in args.data)
    splits = split_text(text)
    print(f"Text: {len(text):,} characters -> train {len(splits.train):,}, "
          f"validation {len(splits.validation):,}, test {len(splits.test):,}")

    tokenizer = ByteBPETokenizer.train([splits.train], vocab_size=args.vocab_size)
    model_config = T0Config(vocab_size=tokenizer.vocab_size)
    t = model_config.context_length
    train_ids = np.array(tokenizer.encode(splits.train), dtype=np.int64)
    train_data = TokenDataset(train_ids, t)
    validation_data = TokenDataset(tokenizer.encode(splits.validation), t)
    test_data = TokenDataset(tokenizer.encode(splits.test), t)
    print(f"Tokenizer: {tokenizer.vocab_size} tokens; train {len(train_data):,} tokens, "
          f"validation {len(validation_data):,}, test {len(test_data):,}")

    model = AuroraT0(model_config, seed=args.seed)
    train_config = TrainConfig(steps=args.steps, batch_size=args.batch_size, max_lr=args.lr,
                               min_lr=args.lr / 10, eval_interval=args.eval_interval, seed=args.seed)
    val_base = baselines(train_ids, validation_data, tokenizer.vocab_size)
    print(f"Model: {model.num_parameters:,} parameters")
    print("Validation baselines: " + ", ".join(f"{k} {v:.3f}" for k, v in val_base.items()))
    print(f"Initial validation loss: {evaluate(model, validation_data, train_config.eval_windows):.3f}\n")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    metrics_file = open(out / "metrics.jsonl", "w", encoding="utf-8")

    def on_eval(entry):
        metrics_file.write(json.dumps(entry) + "\n")
        metrics_file.flush()
        print(f"step {entry['step']:5}  train {entry['train_loss']:.3f}  "
              f"validation {entry['validation_loss']:.3f}  lr {entry['lr']:.2e}  "
              f"|g| {entry['grad_norm']:.2f}  {entry['seconds']:.0f} s", flush=True)

    try:
        result = train(model, train_data, validation_data, train_config, on_eval=on_eval)
    finally:
        metrics_file.close()

    metadata = {"data": [Path(p).name for p in args.data], "train_config": train_config.to_dict()}
    save_checkpoint(out / "last.npz", model, step=train_config.steps, optimizer=result.optimizer,
                    tokenizer=tokenizer, metadata=metadata)
    best = AuroraT0(model_config, params=result.best_params)
    save_checkpoint(out / "best.npz", best, step=result.best_step, tokenizer=tokenizer, metadata=metadata)
    tokenizer.save(out / "tokenizer.json")

    # The test text is used exactly once, for the final numbers.
    test_loss = evaluate(best, test_data)
    test_base = baselines(train_ids, test_data, tokenizer.vocab_size)
    summary = {
        "parameters": best.num_parameters,
        "vocab_size": tokenizer.vocab_size,
        "train_tokens": len(train_data),
        "best_step": result.best_step,
        "validation_loss": result.best_validation_loss,
        "validation_baselines": val_base,
        "test_loss": test_loss,
        "test_perplexity": math.exp(test_loss),
        "test_baselines": test_base,
        "beats_bigram_on_test": test_loss < test_base["bigram"],
        "training_seconds": round(result.seconds, 1),
        "train_config": train_config.to_dict(),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nBest validation loss {result.best_validation_loss:.3f} at step {result.best_step}")
    print(f"Test loss {test_loss:.3f} (perplexity {math.exp(test_loss):.1f}); baselines: "
          + ", ".join(f"{k} {v:.3f}" for k, v in test_base.items()))
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
