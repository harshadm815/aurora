"""
The mathematical operations a Transformer is built from.

Every operation has a forward function and a matching backward function.
A backward function takes the gradient of the loss with respect to the
operation's output (`grad`) plus the forward inputs, and returns the
gradient with respect to each input. Backward passes recompute what they
need from the inputs, so there is no hidden cached state.

The backward functions are not used by the forward-only AURORA-T0 yet;
they exist now so each one can be verified against numerical gradients
(see gradcheck.py) before training is built on top of them.

Conventions: arrays are NumPy arrays; the last axis is the feature axis.
"""

import math

import numpy as np

# ---------------------------------------------------------------- linear


def linear(x: np.ndarray, weight: np.ndarray, bias: np.ndarray | None = None) -> np.ndarray:
    """y = x @ weight + bias, for x of shape (..., in) and weight (in, out)."""
    if weight.ndim != 2:
        raise ValueError(f"weight must be 2-D (in, out), got shape {weight.shape}")
    if x.shape[-1] != weight.shape[0]:
        raise ValueError(
            f"x has {x.shape[-1]} features but weight expects {weight.shape[0]}"
        )
    y = x @ weight
    if bias is not None:
        if bias.shape != (weight.shape[1],):
            raise ValueError(f"bias must have shape ({weight.shape[1]},), got {bias.shape}")
        y = y + bias
    return y


def linear_backward(
    grad: np.ndarray, x: np.ndarray, weight: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Gradients (dx, dweight, dbias) of linear()."""
    dx = grad @ weight.T
    x2 = x.reshape(-1, x.shape[-1])
    g2 = grad.reshape(-1, grad.shape[-1])
    dweight = x2.T @ g2
    dbias = g2.sum(axis=0)
    return dx, dweight, dbias


# ---------------------------------------------------------------- softmax


def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Numerically stable softmax: the maximum is subtracted before exp().

    Entries equal to -inf (masked positions) get probability 0, as long
    as at least one entry along `axis` is finite.
    """
    shifted = x - np.max(x, axis=axis, keepdims=True)
    exps = np.exp(shifted)
    return exps / np.sum(exps, axis=axis, keepdims=True)


def softmax_backward(grad: np.ndarray, y: np.ndarray, axis: int = -1) -> np.ndarray:
    """Gradient of softmax given its output y: y * (grad - sum(grad * y))."""
    return y * (grad - np.sum(grad * y, axis=axis, keepdims=True))


def log_softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    """log(softmax(x)), computed without ever taking log(0)."""
    shifted = x - np.max(x, axis=axis, keepdims=True)
    return shifted - np.log(np.sum(np.exp(shifted), axis=axis, keepdims=True))


def log_softmax_backward(grad: np.ndarray, x: np.ndarray, axis: int = -1) -> np.ndarray:
    """Gradient of log_softmax: grad - softmax(x) * sum(grad)."""
    return grad - softmax(x, axis=axis) * np.sum(grad, axis=axis, keepdims=True)


# ---------------------------------------------------------- cross-entropy


def _check_targets(logits: np.ndarray, targets: np.ndarray) -> None:
    if not np.issubdtype(targets.dtype, np.integer):
        raise ValueError(f"targets must be integers, got {targets.dtype}")
    if targets.shape != logits.shape[:-1]:
        raise ValueError(
            f"targets shape {targets.shape} must equal logits shape without "
            f"the last axis {logits.shape[:-1]}"
        )


def _valid_mask(logits: np.ndarray, targets: np.ndarray, ignore_index: int | None) -> np.ndarray:
    valid = np.ones(targets.shape, dtype=bool) if ignore_index is None else targets != ignore_index
    vocab = logits.shape[-1]
    chosen = targets[valid]
    if chosen.size and (chosen.min() < 0 or chosen.max() >= vocab):
        raise ValueError(f"targets must be in [0, {vocab})")
    return valid


def cross_entropy(
    logits: np.ndarray, targets: np.ndarray, ignore_index: int | None = None
) -> float:
    """Mean negative log-likelihood of `targets` under softmax(logits).

    logits: (..., vocab); targets: integer array of shape (...).
    Positions whose target equals `ignore_index` are left out of the mean.
    """
    _check_targets(logits, targets)
    valid = _valid_mask(logits, targets, ignore_index)
    count = int(valid.sum())
    if count == 0:
        raise ValueError("cross_entropy needs at least one target that isn't ignored")
    logp = log_softmax(logits)
    safe_targets = np.where(valid, targets, 0)
    picked = np.take_along_axis(logp, safe_targets[..., None], axis=-1)[..., 0]
    return float(-np.sum(picked[valid]) / count)


def cross_entropy_backward(
    logits: np.ndarray, targets: np.ndarray, ignore_index: int | None = None
) -> np.ndarray:
    """Gradient of cross_entropy() with respect to the logits:
    (softmax(logits) - one_hot(targets)) / count, zero at ignored positions."""
    _check_targets(logits, targets)
    valid = _valid_mask(logits, targets, ignore_index)
    count = int(valid.sum())
    if count == 0:
        raise ValueError("cross_entropy needs at least one target that isn't ignored")
    grad = softmax(logits)
    safe_targets = np.where(valid, targets, 0)
    np.put_along_axis(
        grad,
        safe_targets[..., None],
        np.take_along_axis(grad, safe_targets[..., None], axis=-1) - 1.0,
        axis=-1,
    )
    grad[~valid] = 0.0
    return grad / count


# ------------------------------------------------------------------- GELU

_GELU_C = math.sqrt(2.0 / math.pi)
_GELU_K = 0.044715


def gelu(x: np.ndarray) -> np.ndarray:
    """GELU activation, tanh approximation (as used by GPT-2):
    0.5 x (1 + tanh(sqrt(2/pi) (x + 0.044715 x^3)))."""
    return 0.5 * x * (1.0 + np.tanh(_GELU_C * (x + _GELU_K * x**3)))


def gelu_backward(grad: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Gradient of gelu()."""
    t = np.tanh(_GELU_C * (x + _GELU_K * x**3))
    derivative = 0.5 * (1.0 + t) + 0.5 * x * (1.0 - t**2) * _GELU_C * (1.0 + 3.0 * _GELU_K * x**2)
    return grad * derivative


# ------------------------------------------------------------- layer norm


def layer_norm(
    x: np.ndarray, gamma: np.ndarray, beta: np.ndarray, eps: float = 1e-5
) -> np.ndarray:
    """Normalise each vector along the last axis to mean 0 and variance 1,
    then scale by gamma and shift by beta (both of shape (features,))."""
    features = x.shape[-1]
    if gamma.shape != (features,) or beta.shape != (features,):
        raise ValueError(f"gamma and beta must have shape ({features},)")
    mean = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mean) / np.sqrt(var + eps) * gamma + beta


def layer_norm_backward(
    grad: np.ndarray, x: np.ndarray, gamma: np.ndarray, eps: float = 1e-5
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Gradients (dx, dgamma, dbeta) of layer_norm()."""
    n = x.shape[-1]
    mean = x.mean(axis=-1, keepdims=True)
    inv_std = 1.0 / np.sqrt(x.var(axis=-1, keepdims=True) + eps)
    x_hat = (x - mean) * inv_std

    dgamma = (grad * x_hat).reshape(-1, n).sum(axis=0)
    dbeta = grad.reshape(-1, n).sum(axis=0)

    dx_hat = grad * gamma
    dx = (inv_std / n) * (
        n * dx_hat
        - dx_hat.sum(axis=-1, keepdims=True)
        - x_hat * (dx_hat * x_hat).sum(axis=-1, keepdims=True)
    )
    return dx, dgamma, dbeta


# --------------------------------------------------------------- RMS norm
# (Phase 16 research variants; AURORA-T0 itself uses layer_norm.)


def rms_norm(x: np.ndarray, gamma: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    """Root-mean-square normalisation: x / sqrt(mean(x^2) + eps) * gamma.
    Like layer norm without subtracting the mean and without a shift."""
    features = x.shape[-1]
    if gamma.shape != (features,):
        raise ValueError(f"gamma must have shape ({features},)")
    return x / np.sqrt((x * x).mean(axis=-1, keepdims=True) + eps) * gamma


def rms_norm_backward(
    grad: np.ndarray, x: np.ndarray, gamma: np.ndarray, eps: float = 1e-5
) -> tuple[np.ndarray, np.ndarray]:
    """Gradients (dx, dgamma) of rms_norm()."""
    n = x.shape[-1]
    inv_rms = 1.0 / np.sqrt((x * x).mean(axis=-1, keepdims=True) + eps)
    x_hat = x * inv_rms
    dgamma = (grad * x_hat).reshape(-1, n).sum(axis=0)
    dx_hat = grad * gamma
    dx = inv_rms * (dx_hat - x_hat * (dx_hat * x_hat).mean(axis=-1, keepdims=True))
    return dx, dgamma


# --------------------------------------------------------- sigmoid / SiLU


def sigmoid(x: np.ndarray) -> np.ndarray:
    """1 / (1 + exp(-x)), computed without overflow for large |x|."""
    out = np.empty_like(x)
    positive = x >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-x[positive]))
    e = np.exp(x[~positive])
    out[~positive] = e / (1.0 + e)
    return out


def sigmoid_backward(grad: np.ndarray, x: np.ndarray) -> np.ndarray:
    s = sigmoid(x)
    return grad * s * (1.0 - s)


def silu(x: np.ndarray) -> np.ndarray:
    """SiLU (swish): x * sigmoid(x), the activation inside SwiGLU."""
    return x * sigmoid(x)


def silu_backward(grad: np.ndarray, x: np.ndarray) -> np.ndarray:
    s = sigmoid(x)
    return grad * (s + x * s * (1.0 - s))
