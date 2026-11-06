"""
Quick look at AURORA-T0:  .venv\\Scripts\\python.exe -m aurora_core

Builds the randomly initialised model, runs one forward pass on some
text (one token per byte) and prints what came out. The model is
untrained, so its predictions are meaningless; the point is to show the
shapes, the parameter count and that the starting loss is close to
log(vocabulary), as it should be for a random model.
"""

import math
import time

import numpy as np

from aurora_core.transformer import AuroraT0, T0Config


def main() -> None:
    config = T0Config()
    model = AuroraT0(config, seed=0)
    print("AURORA-T0 (random initialisation, untrained)")
    for key, value in config.to_dict().items():
        print(f"  {key:15} {value}")
    print(f"  {'parameters':15} {model.num_parameters:,}")

    text = "Hello, I am AURORA."
    data = np.frombuffer(text.encode("utf-8"), dtype=np.uint8).astype(np.int64)
    tokens, targets = data[:-1], data[1:]

    start = time.perf_counter()
    result = model.forward(tokens)
    elapsed = (time.perf_counter() - start) * 1000

    print(f"\nInput: {text!r} ({len(tokens)} tokens)")
    print(f"Logits shape: {result.logits.shape}, attention: {result.attention[0].shape}")
    print(f"Forward pass: {elapsed:.2f} ms")
    print(f"Loss: {model.loss(tokens, targets):.4f}  (random guess: ln {config.vocab_size} = "
          f"{math.log(config.vocab_size):.4f})")


if __name__ == "__main__":
    main()
