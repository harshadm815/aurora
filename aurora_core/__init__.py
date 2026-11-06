"""
aurora_core: AURORA's own neural network, built from first principles.

This package is deliberately separate from the assistant (`aurora/`):
it depends only on Python and NumPy, never imports `aurora`, and is not
used by the running assistant until each part has been built and tested.

    tensors/      Phase 1: the mathematics (forward and backward passes)
                  and numerical gradient checking.
    transformer/  Phase 2: AURORA-T0, a small decoder-only Transformer
                  (forward pass, random initialisation).
    tokenizer/    Phase 3: text <-> token ids (character-level and
                  byte-level BPE), saved as JSON.
    training/     Phase 4: data splits, baselines, AdamW, clipping,
                  learning-rate schedule, checkpoints, training loop.
                  (Backpropagation is transformer/backprop.py.)
    generation/   Phase 5: sampling, text generation, measures of
                  generated text.
    runtime/      Phase 6: the AURORA Core Runtime: model loader, KV-cache
                  inference, generation controller with resource limits.
"""

__version__ = "0.5.0"
