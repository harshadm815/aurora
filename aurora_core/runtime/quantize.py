"""
Lower-precision storage of model weights.

    float32   the normal format: 4 bytes per weight
    float16   2 bytes per weight; about 3 significant decimal digits
    int8      1 byte per weight plus one float32 scale per row/column
              (symmetric, per output channel):
                  scale = max(|w|) / 127 over the channel
                  q     = round(w / scale)            in -127 .. 127
                  w'    = q * scale                   (when loaded)

Only matrices and embeddings are reduced; biases and layer-norm
parameters (a tiny share of the weights) stay float32.

Precision here is about *storage*: when a checkpoint is loaded, the
weights are turned back into the model's float32, so memory use while
running and speed are unchanged (NumPy has no fast int8 matrix product).
What changes is the file size, and the quality, which is measured rather
than assumed.

Channels: a weight matrix (in, out) gets one scale per output column; an
embedding (rows, d) gets one scale per row (each row is one token's or
one position's vector).
"""

import numpy as np

PRECISIONS = ("float32", "float16", "int8")


def is_quantizable(name: str, value: np.ndarray) -> bool:
    return value.ndim == 2


def channel_axis(name: str) -> int:
    """Axis that is reduced when computing scales: 0 for (in, out) weight
    matrices (one scale per column), 1 for embeddings (one per row)."""
    return 1 if name.endswith("embedding") else 0


def quantize_int8(w: np.ndarray, axis: int) -> tuple[np.ndarray, np.ndarray]:
    w = np.asarray(w, dtype=np.float32)
    peak = np.max(np.abs(w), axis=axis, keepdims=True)
    scale = np.where(peak > 0, peak / 127.0, 1.0).astype(np.float32)
    q = np.clip(np.rint(w / scale), -127, 127).astype(np.int8)
    return q, scale


def dequantize_int8(q: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return q.astype(np.float32) * scale


def encode_params(params: dict[str, np.ndarray], precision: str) -> dict[str, np.ndarray]:
    """Arrays to store for these parameters: "param/<name>" (float32 or
    float16), or "qparam/<name>" + "qscale/<name>" for int8."""
    if precision not in PRECISIONS:
        raise ValueError(f"precision must be one of {PRECISIONS}")
    arrays = {}
    for name, value in params.items():
        if precision == "float32" or not is_quantizable(name, value):
            arrays["param/" + name] = value
        elif precision == "float16":
            if np.max(np.abs(value)) > np.finfo(np.float16).max:
                raise ValueError(f"{name} has values too large for float16")
            arrays["param/" + name] = value.astype(np.float16)
        else:
            q, scale = quantize_int8(value, channel_axis(name))
            arrays["qparam/" + name] = q
            arrays["qscale/" + name] = scale
    return arrays


def decode_params(arrays: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Parameters (as float32 or the stored float type) from encode_params output."""
    params = {k[len("param/"):]: v for k, v in arrays.items() if k.startswith("param/")}
    for key, q in arrays.items():
        if key.startswith("qparam/"):
            name = key[len("qparam/"):]
            scale = arrays.get("qscale/" + name)
            if scale is None or q.dtype != np.int8:
                raise ValueError(f"quantized parameter {name} is incomplete")
            params[name] = dequantize_int8(q, scale)
    return params
