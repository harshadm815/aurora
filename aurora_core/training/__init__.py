"""Training AURORA-T0: data, baselines, optimiser, checkpoints, training loop."""

from aurora_core.training.baselines import bigram_loss, unigram_loss, uniform_loss
from aurora_core.training.checkpoint import (
    Checkpoint,
    CheckpointError,
    load_checkpoint,
    save_checkpoint,
)
from aurora_core.training.data import (
    TextSplits,
    TokenDataset,
    read_text,
    split_text,
    strip_gutenberg,
)
from aurora_core.training.optim import AdamW, clip_grad_norm, global_grad_norm, learning_rate
from aurora_core.training.trainer import (
    TrainConfig,
    TrainingDiverged,
    TrainResult,
    evaluate,
    train,
)

__all__ = [
    "AdamW", "Checkpoint", "CheckpointError", "TextSplits", "TokenDataset", "TrainConfig",
    "TrainResult", "TrainingDiverged", "bigram_loss", "clip_grad_norm", "evaluate",
    "global_grad_norm", "learning_rate", "load_checkpoint", "read_text", "save_checkpoint",
    "split_text", "strip_gutenberg", "train", "unigram_loss", "uniform_loss",
]
