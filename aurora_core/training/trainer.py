"""
The training loop.

Each step: take a random batch of windows from the training data, run
the forward and backward passes, clip the gradient, set the learning
rate from the schedule and update the weights with AdamW. Every
eval_interval steps the validation loss is measured on fixed windows
of the validation data, which training never sees, and the best model
so far (by validation loss) is kept.
"""

import math
import time
from dataclasses import asdict, dataclass

import numpy as np

from aurora_core.tensors.ops import cross_entropy
from aurora_core.training.data import TokenDataset
from aurora_core.training.optim import AdamW, clip_grad_norm, learning_rate
from aurora_core.transformer import AuroraT0
from aurora_core.transformer.backprop import loss_and_gradients


class TrainingDiverged(RuntimeError):
    """The loss became NaN or infinite; the weights are no longer usable."""


@dataclass(frozen=True)
class TrainConfig:
    steps: int = 3000
    batch_size: int = 32
    max_lr: float = 3e-3
    min_lr: float = 3e-4
    warmup_steps: int = 100
    weight_decay: float = 0.1
    grad_clip: float = 1.0
    eval_interval: int = 250
    eval_windows: int = 200
    seed: int = 0

    def __post_init__(self):
        for name in ("steps", "batch_size", "eval_interval", "eval_windows"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.warmup_steps < 0 or not 0 < self.min_lr <= self.max_lr or self.grad_clip <= 0:
            raise ValueError("invalid learning-rate or clipping settings")

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(model: AuroraT0, dataset: TokenDataset, max_windows: int | None = None, batch_size: int = 64) -> float:
    """Mean next-token loss over fixed windows of `dataset` (no training)."""
    inputs, targets = dataset.windows(max_windows)
    total, count = 0.0, 0
    for start in range(0, len(inputs), batch_size):
        x, y = inputs[start:start + batch_size], targets[start:start + batch_size]
        total += cross_entropy(model(x), y) * y.size
        count += y.size
    return total / count


@dataclass
class TrainResult:
    history: list[dict]          # one entry per evaluation
    best_validation_loss: float
    best_step: int
    best_params: dict[str, np.ndarray]
    optimizer: AdamW
    seconds: float


def train(model: AuroraT0, train_data: TokenDataset, validation_data: TokenDataset,
          config: TrainConfig, on_eval=None, optimizer: AdamW | None = None,
          start_step: int = 0) -> TrainResult:
    """Train `model` in place. on_eval(entry) is called after each evaluation."""
    if train_data.context_length > model.config.context_length:
        raise ValueError("data windows are longer than the model's context")
    rng = np.random.default_rng(config.seed + start_step)
    optimizer = optimizer or AdamW(model.params, lr=config.max_lr, weight_decay=config.weight_decay)

    history: list[dict] = []
    best_loss, best_step = math.inf, start_step
    best_params = {k: v.copy() for k, v in model.params.items()}
    recent: list[float] = []
    started = time.perf_counter()

    # Research variants (aurora_core.research) bring their own backward pass.
    gradients = getattr(model, "loss_and_gradients", None) or (lambda x, y: loss_and_gradients(model, x, y))

    for step in range(start_step, config.steps):
        inputs, targets = train_data.random_batch(rng, config.batch_size)
        loss, grads = gradients(inputs, targets)
        if not math.isfinite(loss):
            raise TrainingDiverged(f"loss became {loss} at step {step}")
        norm = clip_grad_norm(grads, config.grad_clip)
        lr = learning_rate(step, config.max_lr, config.warmup_steps, config.steps, config.min_lr)
        optimizer.step(model.params, grads, lr=lr)
        recent.append(loss)

        done = step + 1
        if done % config.eval_interval == 0 or done == config.steps:
            val = evaluate(model, validation_data, config.eval_windows)
            entry = {
                "step": done,
                "train_loss": float(np.mean(recent)),
                "validation_loss": val,
                "lr": lr,
                "grad_norm": norm,
                "seconds": round(time.perf_counter() - started, 2),
            }
            recent = []
            if val < best_loss:
                best_loss, best_step = val, done
                best_params = {k: v.copy() for k, v in model.params.items()}
            history.append(entry)
            if on_eval:
                on_eval(entry)

    return TrainResult(history, best_loss, best_step, best_params, optimizer,
                       time.perf_counter() - started)
