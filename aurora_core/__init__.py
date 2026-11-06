"""
aurora_core: AURORA's own neural network, built from first principles.

This package is deliberately separate from the assistant (`aurora/`):
it depends only on Python and NumPy, never imports `aurora`, and is not
used by the running assistant until each part has been built and tested.

    tensors/      Phase 1: the mathematics (forward and backward passes)
                  and numerical gradient checking.
    transformer/  Phase 2: AURORA-T0, a small decoder-only Transformer
                  (forward pass, random initialisation).
"""

__version__ = "0.1.0"
