"""
Generation speed benchmarks:

    .venv\\Scripts\\python.exe -m aurora_core.runtime.benchmark --model Models\\aurora_t0_lr1e-2\\best.npz

For each model, the same prompt and sampling settings (temperature 0.5,
top-k 40, fixed seed) are run through three implementations:

    no cache    the Phase 5 generator: a full forward pass over the
                window for every token (generation.generate_ids)
    reference   the runtime's plain Phase 6 session and sampler
    fast        the runtime's optimised Phase 7 session and sampler

The two runtime engines must choose identical tokens; the benchmark
checks and reports this. Speeds are the median of several runs.

Besides the trained AURORA-T0, randomly initialised larger models are
measured too (speed doesn't depend on whether weights are trained), to
see how each implementation scales with model size and context length.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from aurora_core.generation.generate import GenerationConfig, generate_ids
from aurora_core.runtime.runtime import AuroraRuntime, RuntimeLimits
from aurora_core.tokenizer import ByteBPETokenizer
from aurora_core.training.checkpoint import load_checkpoint, save_checkpoint
from aurora_core.transformer import AuroraT0, T0Config

LARGER = {
    "wide (d 128, 4 layers, context 128)": dict(d_model=128, n_heads=4, n_layers=4, d_ff=512, context_length=128),
    "medium (d 256, 6 layers, context 256)": dict(d_model=256, n_heads=8, n_layers=6, d_ff=1024, context_length=256),
}


def _median_seconds(fn, repeats: int) -> float:
    fn()                                   # warm-up
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
    return statistics.median(times)


def benchmark_model(path, tokens: int, repeats: int, prompt: str = "Sherlock Holmes said", include_no_cache=True) -> dict:
    config = GenerationConfig(max_new_tokens=tokens, temperature=0.5, top_k=40, seed=0, stop_at_eos=False)
    result = {"tokens": tokens}
    outputs = {}
    for engine in ("reference", "fast"):
        rt = AuroraRuntime(RuntimeLimits(engine=engine))
        rt.load(path)
        seconds = _median_seconds(lambda: outputs.__setitem__(engine, rt.generate(prompt, config).token_ids), repeats)
        result[engine] = tokens / seconds
        result["memory_bytes"] = rt.memory_usage()
        result["parameters"] = rt.info.parameters
        model, tok = rt._model, rt.tokenizer
    if include_no_cache:
        seconds = _median_seconds(lambda: generate_ids(model, tok.encode(prompt), config), max(1, repeats // 2))
        result["no_cache"] = tokens / seconds
    result["identical_tokens"] = outputs["reference"] == outputs["fast"]
    result["speedup_fast_vs_reference"] = result["fast"] / result["reference"]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core.runtime.benchmark")
    parser.add_argument("--model", help="a trained checkpoint to include")
    parser.add_argument("--tokens", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--no-larger", action="store_true", help="skip the larger random models")
    parser.add_argument("--out", default="Models/benchmarks/runtime.json")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    models = []
    if args.model:
        models.append(("AURORA-T0 (trained)", Path(args.model)))
    if not args.no_larger:
        tok = ByteBPETokenizer()
        if args.model:
            tok = load_checkpoint(args.model).tokenizer
        for name, sizes in LARGER.items():
            path = out.parent / f"random_{sizes['d_model']}x{sizes['n_layers']}.npz"
            save_checkpoint(path, AuroraT0(T0Config(vocab_size=tok.vocab_size, **sizes), seed=0), tokenizer=tok)
            models.append((name, path))

    print(f"{'model':40}{'params':>11}{'no cache':>10}{'reference':>11}{'fast':>9}{'fast/ref':>10}  same")
    results = []
    for name, path in models:
        r = benchmark_model(path, args.tokens, args.repeats)
        r["model"] = name
        results.append(r)
        print(f"{name:40}{r['parameters']:11,}{r['no_cache']:10.0f}{r['reference']:11.0f}{r['fast']:9.0f}"
              f"{r['speedup_fast_vs_reference']:9.2f}x  {'yes' if r['identical_tokens'] else 'NO'}", flush=True)
    out.write_text(json.dumps({"tokens": args.tokens, "repeats": args.repeats, "results": results}, indent=2),
                   encoding="utf-8")
    print(f"\n(tokens per second, {args.tokens} tokens, median of {args.repeats}) Saved to {out}")
    return 0 if all(r["identical_tokens"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
