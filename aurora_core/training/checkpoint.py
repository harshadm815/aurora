"""
Checkpoints: everything needed to use or continue training a model, in
one .npz file.

Contents: the model configuration, every parameter, the optimizer's
state (so training can resume exactly), the training step, the
tokenizer, and free-form metadata (metrics, data source).

Saved with NumPy's .npz format and loaded with allow_pickle=False, so a
checkpoint can only contain numbers and text and loading one can't run
code. It is written to a temporary file and renamed, so an interrupted
save never destroys the previous checkpoint.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aurora_core.tokenizer import Tokenizer
from aurora_core.transformer import AuroraT0, T0Config

FORMAT = "aurora-checkpoint"
VERSION = 1


class CheckpointError(ValueError):
    """A checkpoint file is missing, damaged or of an unknown format."""


@dataclass
class Checkpoint:
    model: AuroraT0
    step: int = 0
    optimizer_state: dict | None = None
    tokenizer: Tokenizer | None = None
    metadata: dict = field(default_factory=dict)


def save_checkpoint(path, model: AuroraT0, *, step: int = 0, optimizer=None,
                    tokenizer: Tokenizer | None = None, metadata: dict | None = None) -> None:
    header = {
        "format": FORMAT,
        "version": VERSION,
        "config": model.config.to_dict(),
        "step": int(step),
        "has_optimizer": optimizer is not None,
        "optimizer_t": optimizer.t if optimizer is not None else 0,
        "metadata": metadata or {},
    }
    arrays = {"header": np.array(json.dumps(header))}
    if tokenizer is not None:
        arrays["tokenizer"] = np.array(tokenizer.to_json())
    for name, value in model.params.items():
        arrays["param/" + name] = value
    if optimizer is not None:
        for name in model.params:
            arrays["adam_m/" + name] = optimizer.m[name]
            arrays["adam_v/" + name] = optimizer.v[name]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        np.savez(f, **arrays)
    os.replace(tmp, path)


def load_checkpoint(path) -> Checkpoint:
    path = Path(path)
    if not path.is_file():
        raise CheckpointError(f"no checkpoint at {path}")
    try:
        with np.load(path, allow_pickle=False) as archive:
            arrays = {key: archive[key] for key in archive.files}
    except Exception as error:   # zip / format errors of many kinds
        raise CheckpointError(f"checkpoint can't be read: {type(error).__name__}: {error}") from None

    try:
        header = json.loads(str(arrays.pop("header")))
    except (KeyError, json.JSONDecodeError):
        raise CheckpointError("checkpoint has no valid header") from None
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise CheckpointError("not an AURORA checkpoint")
    if header.get("version") != VERSION:
        raise CheckpointError(f"unsupported checkpoint version {header.get('version')!r}")

    try:
        config = T0Config(**header["config"])
        params = {k[len("param/"):]: v for k, v in arrays.items() if k.startswith("param/")}
        params = {k: v.astype(config.np_dtype, copy=False) for k, v in params.items()}
        model = AuroraT0(config, params=params)
    except (KeyError, TypeError, ValueError) as error:
        raise CheckpointError(f"checkpoint model is invalid: {error}") from None

    optimizer_state = None
    if header.get("has_optimizer"):
        try:
            optimizer_state = {
                "t": int(header["optimizer_t"]),
                "m": {name: arrays["adam_m/" + name] for name in params},
                "v": {name: arrays["adam_v/" + name] for name in params},
            }
        except KeyError as error:
            raise CheckpointError(f"checkpoint optimizer state is incomplete: {error}") from None

    try:
        tokenizer = Tokenizer.from_json(str(arrays["tokenizer"])) if "tokenizer" in arrays else None
    except ValueError as error:
        raise CheckpointError(f"checkpoint tokenizer is invalid: {error}") from None
    if tokenizer is not None and tokenizer.vocab_size != config.vocab_size:
        raise CheckpointError("checkpoint tokenizer and model have different vocabulary sizes")

    return Checkpoint(
        model=model,
        step=int(header.get("step", 0)),
        optimizer_state=optimizer_state,
        tokenizer=tokenizer,
        metadata=header.get("metadata") or {},
    )
