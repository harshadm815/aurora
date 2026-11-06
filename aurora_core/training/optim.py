"""
Optimisation: AdamW, gradient clipping and the learning-rate schedule.
"""

import math

import numpy as np


def global_grad_norm(grads: dict[str, np.ndarray]) -> float:
    return math.sqrt(sum(float(np.sum(np.square(g, dtype=np.float64))) for g in grads.values()))


def clip_grad_norm(grads: dict[str, np.ndarray], max_norm: float) -> float:
    """Scale all gradients down together if their combined (L2) norm is
    above max_norm, so one bad batch can't throw the weights far off.
    Changes grads in place; returns the norm before clipping."""
    if max_norm <= 0:
        raise ValueError("max_norm must be positive")
    norm = global_grad_norm(grads)
    if norm > max_norm:
        factor = max_norm / (norm + 1e-12)
        for g in grads.values():
            g *= factor
    return norm


def default_decay(name: str, value: np.ndarray) -> bool:
    """Weight decay applies to weight matrices and embeddings, not to
    biases or layer-norm scales/shifts (shrinking those has no useful
    regularising effect)."""
    return value.ndim >= 2


class AdamW:
    """Adam with decoupled weight decay (Loshchilov & Hutter, 2019).

    For each parameter p with gradient g, at step t:
        m = b1 m + (1 - b1) g
        v = b2 v + (1 - b2) g^2
        p -= lr * weight_decay * p                       (if decayed)
        p -= lr * (m / (1 - b1^t)) / (sqrt(v / (1 - b2^t)) + eps)
    """

    def __init__(
        self,
        params: dict[str, np.ndarray],
        lr: float = 1e-3,
        betas: tuple[float, float] = (0.9, 0.95),
        eps: float = 1e-8,
        weight_decay: float = 0.1,
        decay=default_decay,
    ):
        if lr <= 0 or not (0 <= betas[0] < 1 and 0 <= betas[1] < 1) or eps <= 0 or weight_decay < 0:
            raise ValueError("invalid AdamW hyperparameters")
        self.lr = lr
        self.betas = betas
        self.eps = eps
        self.weight_decay = weight_decay
        self.decayed = {name for name, value in params.items() if decay(name, value)}
        self.m = {name: np.zeros_like(value) for name, value in params.items()}
        self.v = {name: np.zeros_like(value) for name, value in params.items()}
        self.t = 0

    def step(self, params: dict[str, np.ndarray], grads: dict[str, np.ndarray], lr: float | None = None) -> None:
        """Update params in place."""
        lr = self.lr if lr is None else lr
        b1, b2 = self.betas
        self.t += 1
        c1 = 1.0 - b1**self.t
        c2 = 1.0 - b2**self.t
        for name, p in params.items():
            g = grads[name]
            m, v = self.m[name], self.v[name]
            m *= b1
            m += (1.0 - b1) * g
            v *= b2
            v += (1.0 - b2) * g * g
            if name in self.decayed and self.weight_decay:
                p -= lr * self.weight_decay * p
            p -= lr * (m / c1) / (np.sqrt(v / c2) + self.eps)

    def state(self) -> dict:
        return {"t": self.t, "m": self.m, "v": self.v}

    def load_state(self, state: dict) -> None:
        for key in ("m", "v"):
            if state[key].keys() != self.m.keys():
                raise ValueError("optimizer state doesn't match the model's parameters")
            for name, value in state[key].items():
                if value.shape != self.m[name].shape:
                    raise ValueError(f"optimizer state for {name} has the wrong shape")
        self.t = int(state["t"])
        self.m = {k: np.array(v, dtype=self.m[k].dtype) for k, v in state["m"].items()}
        self.v = {k: np.array(v, dtype=self.v[k].dtype) for k, v in state["v"].items()}


def learning_rate(step: int, max_lr: float, warmup_steps: int, total_steps: int, min_lr: float = 0.0) -> float:
    """Linear warm-up to max_lr over warmup_steps, then cosine decay to
    min_lr at total_steps (and min_lr after that).

    Warm-up avoids large, badly-directed updates while Adam's running
    averages are still empty; the decay lets training settle at the end.
    """
    if step < warmup_steps:
        return max_lr * (step + 1) / warmup_steps
    if step >= total_steps:
        return min_lr
    progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
    return min_lr + 0.5 * (max_lr - min_lr) * (1.0 + math.cos(math.pi * progress))
