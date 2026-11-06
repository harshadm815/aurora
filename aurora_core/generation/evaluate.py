"""
Evaluate a trained model's generated text:

    .venv\\Scripts\\python.exe -m aurora_core.generation.evaluate ^
        --checkpoint Models\\aurora_t0\\best.npz --data Data\\pg1661.txt

Prompts are taken from the *validation* text (never trained on; the test
text stays reserved for final loss numbers). For each prompt the model
continues it at several temperatures, the bigram baseline continues it
too, and the real continuation from the book is kept. All three are
measured with aurora_core.generation.quality.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from aurora_core.generation.generate import BANNED, GenerationConfig, generate_ids
from aurora_core.generation.quality import BigramSampler, Reference, average_stats
from aurora_core.training import load_checkpoint, read_text, split_text, strip_gutenberg


def evaluate_generation(model, tokenizer, splits, prompts: int = 20, prompt_tokens: int = 16,
                        new_tokens: int = 100, temperatures=(0.0, 0.5, 0.8, 1.0), seed: int = 0) -> dict:
    reference = Reference(splits.train)
    train_ids = np.array(tokenizer.encode(splits.train), dtype=np.int64)
    val_ids = tokenizer.encode(splits.validation)
    span = prompt_tokens + new_tokens
    if len(val_ids) < span * prompts:
        raise ValueError("validation text is too short for this many prompts")
    starts = np.linspace(0, len(val_ids) - span, prompts).astype(int)
    cases = [(val_ids[s:s + prompt_tokens], val_ids[s + prompt_tokens:s + span]) for s in starts]

    results: dict = {"settings": {"prompts": prompts, "prompt_tokens": prompt_tokens,
                                  "new_tokens": new_tokens, "seed": seed}, "systems": {}}

    def record(name, outputs):
        texts = [tokenizer.decode(ids) for ids in outputs]
        results["systems"][name] = {
            "stats": average_stats(texts, reference),
            "samples": [{"prompt": tokenizer.decode(p), "continuation": t}
                        for (p, _), t in list(zip(cases, texts))[:3]],
        }

    record("real text (validation)", [real for _, real in cases])
    rng = np.random.default_rng(seed)
    bigram = BigramSampler(train_ids, tokenizer.vocab_size)
    record("bigram baseline", [bigram.generate(p, new_tokens, rng, banned=BANNED) for p, _ in cases])
    for t in temperatures:
        outputs = [generate_ids(model, p, GenerationConfig(max_new_tokens=new_tokens, temperature=t,
                                                           top_k=None, seed=seed + i, stop_at_eos=False))
                   for i, (p, _) in enumerate(cases)]
        record(f"AURORA-T0, temperature {t}", outputs)
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core.generation.evaluate")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--data", nargs="+", required=True, help="the text the model was trained on")
    parser.add_argument("--out", help="where to save the results (JSON); default next to the checkpoint")
    parser.add_argument("--prompts", type=int, default=20)
    args = parser.parse_args(argv)

    ckpt = load_checkpoint(args.checkpoint)
    if ckpt.tokenizer is None:
        print("The checkpoint has no tokenizer.")
        return 1
    text = "\n".join(strip_gutenberg(read_text(p)) for p in args.data)
    results = evaluate_generation(ckpt.model, ckpt.tokenizer, split_text(text), prompts=args.prompts)

    keys = ["known_word_rate", "distinct_2", "repeated_4gram_rate", "copied_6gram_rate"]
    print(f"{'':32}" + "".join(f"{k:>21}" for k in keys))
    for name, entry in results["systems"].items():
        print(f"{name:32}" + "".join(f"{entry['stats'][k]:21.3f}" for k in keys))

    out = Path(args.out) if args.out else Path(args.checkpoint).with_name("generation_eval.json")
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved results and sample texts to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
