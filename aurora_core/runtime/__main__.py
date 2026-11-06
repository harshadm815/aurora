"""
Run an AURORA model with the AURORA Core Runtime:

    .venv\\Scripts\\python.exe -m aurora_core.runtime --model Models\\aurora_t0_lr1e-2\\best.npz --prompt "Sherlock Holmes"
    .venv\\Scripts\\python.exe -m aurora_core.runtime --model ... --interactive
    .venv\\Scripts\\python.exe -m aurora_core.runtime --model ... --benchmark
"""

import argparse
import sys
import time

import numpy as np

from aurora_core.generation.generate import GenerationConfig, generate_ids
from aurora_core.runtime import AuroraRuntime, ModelLoadError, RuntimeLimits


def show_info(runtime: AuroraRuntime) -> None:
    info, mem = runtime.info, runtime.memory_usage()
    cfg = info.config
    print(f"Model: {info.path}")
    print(f"  sha256 {info.sha256[:16]}…  {info.file_bytes / 1024:.0f} KB on disk, "
          f"{info.parameters:,} parameters, trained {info.training_step} steps")
    print(f"  {cfg['n_layers']} layer(s), {cfg['n_heads']} heads, d_model {cfg['d_model']}, "
          f"context {cfg['context_length']}, vocabulary {cfg['vocab_size']} ({info.tokenizer})")
    print(f"  memory: weights {mem['parameters'] / 1024:.0f} KB + KV cache {mem['kv_cache'] / 1024:.0f} KB")


def run_once(runtime: AuroraRuntime, prompt: str, config: GenerationConfig) -> None:
    print(prompt, end="", flush=True)
    result = runtime.generate(prompt, config, on_text=lambda piece: print(piece, end="", flush=True))
    s = result.stats
    print(f"\n[{s.new_tokens} tokens, {s.tokens_per_second:.0f} tokens/s, stopped: {s.stop_reason}"
          + (f", prompt shortened by {s.prompt_truncated} tokens" if s.prompt_truncated else "") + "]")


def benchmark(runtime: AuroraRuntime, prompt: str, tokens: int) -> None:
    """Greedy generation with the runtime (KV cache) vs the reference
    generator (full forward pass per token), same prompt and length."""
    model, tokenizer = runtime._model, runtime.tokenizer
    config = GenerationConfig(max_new_tokens=tokens, temperature=0, top_k=None, stop_at_eos=False)
    prompt_ids = tokenizer.encode(prompt)

    start = time.perf_counter()
    reference = generate_ids(model, prompt_ids, config)
    reference_seconds = time.perf_counter() - start

    result = runtime.generate(prompt, config)
    s = result.stats
    cached_seconds = s.prefill_seconds + s.generation_seconds
    same = sum(a == b for a, b in zip(reference, result.token_ids))
    context = model.config.context_length
    within = min(tokens, context - len(prompt_ids))
    print(f"\nBenchmark: {tokens} tokens, greedy, prompt {len(prompt_ids)} tokens")
    print(f"  reference (no cache): {reference_seconds:.3f} s = {tokens / reference_seconds:.0f} tokens/s")
    print(f"  runtime (KV cache):   {cached_seconds:.3f} s = {tokens / cached_seconds:.0f} tokens/s "
          f"({reference_seconds / cached_seconds:.1f}x), {s.cache_rebuilds} cache rebuilds")
    first_diff = next((i for i, (a, b) in enumerate(zip(reference, result.token_ids)) if a != b), None)
    print(f"  identical tokens: {same}/{tokens}"
          + (f" (first difference at token {first_diff}; within the first full window of "
             f"{within} tokens both must agree)" if first_diff is not None else ""))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core.runtime")
    parser.add_argument("--model", required=True, help="a checkpoint (.npz) with its tokenizer")
    parser.add_argument("--prompt", default="Sherlock Holmes")
    parser.add_argument("--tokens", type=int, default=150)
    parser.add_argument("--temperature", type=float, default=0.5)
    parser.add_argument("--top-k", type=int, default=40)
    parser.add_argument("--top-p", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--keep", type=int, help="tokens kept when the context is full (default: half)")
    parser.add_argument("--interactive", action="store_true", help="read prompts until an empty line")
    parser.add_argument("--benchmark", action="store_true", help="compare speed with the uncached reference")
    args = parser.parse_args(argv)

    runtime = AuroraRuntime(RuntimeLimits(keep_on_overflow=args.keep))
    try:
        runtime.load(args.model)
    except ModelLoadError as error:
        print(f"Can't load the model: {error}")
        return 1
    show_info(runtime)
    print()

    if args.benchmark:
        benchmark(runtime, args.prompt, args.tokens)
        return 0
    config = GenerationConfig(max_new_tokens=args.tokens, temperature=args.temperature,
                              top_k=args.top_k or None, top_p=args.top_p, seed=args.seed)
    if not args.interactive:
        run_once(runtime, args.prompt, config)
        return 0
    print("Type a beginning of text and AURORA-T0 continues it. Empty line to quit.")
    while True:
        try:
            prompt = input("\n> ")
        except EOFError:
            break
        if not prompt.strip():
            break
        run_once(runtime, prompt, config)
    return 0


if __name__ == "__main__":
    np.seterr(all="raise")   # any numerical problem stops the run instead of producing NaN
    sys.exit(main())
