"""
Compare storage precisions for a trained model:

    .venv\\Scripts\\python.exe -m aurora_core.runtime.precision ^
        --model Models\\aurora_t0_lr1e-2\\best.npz --data Data\\pg1661.txt

Writes <model>_float16.npz and <model>_int8.npz next to the model and
reports, for each precision:
    file size
    validation and test loss (the same held-out text as training used)
    agreement: how often greedy next-token choices equal float32's, over
               every position of the validation windows
    largest weight error after the round trip
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from aurora_core.training import (
    TokenDataset,
    evaluate,
    load_checkpoint,
    read_text,
    save_checkpoint,
    split_text,
    strip_gutenberg,
)


def compare_precisions(model_path, text: str) -> list[dict]:
    model_path = Path(model_path)
    base = load_checkpoint(model_path)
    tok, cfg = base.tokenizer, base.model.config
    splits = split_text(text)
    val = TokenDataset(tok.encode(splits.validation), cfg.context_length)
    test = TokenDataset(tok.encode(splits.test), cfg.context_length)
    windows, _ = val.windows()
    reference_choice = base.model(windows).argmax(-1)

    rows = []
    for precision in ("float32", "float16", "int8"):
        path = model_path if precision == "float32" else model_path.with_name(f"{model_path.stem}_{precision}.npz")
        if precision != "float32":
            save_checkpoint(path, base.model, step=base.step, tokenizer=tok, precision=precision,
                            metadata={**base.metadata, "converted_from": model_path.name})
        model = load_checkpoint(path).model
        error = max(float(np.max(np.abs(model.params[k] - v))) for k, v in base.model.params.items())
        rows.append({
            "precision": precision,
            "file": path.name,
            "file_bytes": path.stat().st_size,
            "validation_loss": evaluate(model, val),
            "test_loss": evaluate(model, test),
            "greedy_agreement": float(np.mean(model(windows).argmax(-1) == reference_choice)),
            "max_weight_error": error,
        })
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core.runtime.precision")
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", nargs="+", required=True, help="the text the model was trained on")
    args = parser.parse_args(argv)
    text = "\n".join(strip_gutenberg(read_text(p)) for p in args.data)
    rows = compare_precisions(args.model, text)
    print(f"{'precision':10}{'file':>10}{'val loss':>10}{'test loss':>11}{'agreement':>11}{'max err':>11}")
    for r in rows:
        print(f"{r['precision']:10}{r['file_bytes'] / 1024:8.0f} KB{r['validation_loss']:10.4f}"
              f"{r['test_loss']:11.4f}{r['greedy_agreement']:10.1%}{r['max_weight_error']:11.2e}")
    out = Path(args.model).with_name("precision.json")
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nSaved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
