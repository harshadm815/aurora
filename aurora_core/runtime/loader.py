"""
Loading a model for the runtime.

Checks before anything is read into memory:
  - the file exists and is no larger than the runtime's limit;
then, while loading (through training.checkpoint, which deserialises
the weights without pickle and validates every part):
  - a SHA-256 fingerprint of the file is computed, so it is always known
    exactly which weights produced an answer;
  - the checkpoint must contain its tokenizer, matching the vocabulary.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from aurora_core.tokenizer import Tokenizer
from aurora_core.training.checkpoint import CheckpointError, load_checkpoint
from aurora_core.transformer import AuroraT0


class ModelLoadError(RuntimeError):
    """The model file can't be used by the runtime."""


@dataclass(frozen=True)
class ModelInfo:
    path: str
    sha256: str
    file_bytes: int
    parameters: int
    parameter_bytes: int
    config: dict
    tokenizer: str
    training_step: int
    metadata: dict


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_model(path, max_file_bytes: int) -> tuple[AuroraT0, Tokenizer, ModelInfo]:
    path = Path(path)
    if not path.is_file():
        raise ModelLoadError(f"no model file at {path}")
    size = path.stat().st_size
    if size > max_file_bytes:
        raise ModelLoadError(
            f"model file is {size / 1e6:.1f} MB; the runtime's limit is {max_file_bytes / 1e6:.1f} MB"
        )
    try:
        checkpoint = load_checkpoint(path)
    except CheckpointError as error:
        raise ModelLoadError(str(error)) from None
    if checkpoint.tokenizer is None:
        raise ModelLoadError("the checkpoint has no tokenizer, so text can't be encoded")

    model = checkpoint.model
    info = ModelInfo(
        path=str(path.resolve()),
        sha256=_sha256(path),
        file_bytes=size,
        parameters=model.num_parameters,
        parameter_bytes=int(sum(p.nbytes for p in model.params.values())),
        config=model.config.to_dict(),
        tokenizer=checkpoint.tokenizer.kind,
        training_step=checkpoint.step,
        metadata=checkpoint.metadata,
    )
    return model, checkpoint.tokenizer, info
