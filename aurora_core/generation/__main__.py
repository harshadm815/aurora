"""
Generate text with a trained AURORA model:

    .venv\\Scripts\\python.exe -m aurora_core.generation ^
        --checkpoint Models\\aurora_t0\\best.npz --prompt "Sherlock Holmes"
"""

import argparse
import sys

from aurora_core.generation.generate import GenerationConfig, generate_text
from aurora_core.training import load_checkpoint


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora_core.generation")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--prompt", default="Sherlock Holmes")
    parser.add_argument("--tokens", type=int, default=150, help="how many new tokens")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--top-k", type=int, default=40)
    parser.add_argument("--top-p", type=float)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)

    ckpt = load_checkpoint(args.checkpoint)
    if ckpt.tokenizer is None:
        print("The checkpoint has no tokenizer.")
        return 1
    config = GenerationConfig(max_new_tokens=args.tokens, temperature=args.temperature,
                              top_k=args.top_k or None, top_p=args.top_p, seed=args.seed)
    print(args.prompt, end="", flush=True)
    generate_text(ckpt.model, ckpt.tokenizer, args.prompt, config,
                  on_text=lambda piece: print(piece, end="", flush=True))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
