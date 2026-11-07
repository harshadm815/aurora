"""
What the Model manager and Training monitor show, without any GUI code
(so it can be tested on its own).

    find_core_models()     every AURORA checkpoint (.npz) under Models/,
                           read from its header only (no weights loaded)
    find_training_runs()   every folder with a metrics.jsonl: finished
                           (summary.json written) or still running
    read_metrics()         a metrics.jsonl, tolerating a half-written last line
    training_command()     how to start a training run as a separate process,
                           both from source and from the packaged .exe
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from aurora.config import settings

MAX_HEADER_BYTES = 1_000_000


@dataclass
class CoreModel:
    path: Path
    name: str                       # "aurora_t0_lr1e-2/best.npz"
    size: int
    modified: datetime
    readable: bool = True
    problem: str = ""
    config: dict = field(default_factory=dict)
    step: int = 0
    precision: str = "float32"
    parameters: int = 0
    has_tokenizer: bool = False
    has_optimizer: bool = False
    summary: dict = field(default_factory=dict)      # summary.json of its training run, if any

    def describe(self) -> str:
        if not self.readable:
            return f"unreadable: {self.problem}"
        c = self.config
        return (f"{self.parameters:,} parameters · {c.get('n_layers')} layer(s), d={c.get('d_model')}, "
                f"context {c.get('context_length')}, vocabulary {c.get('vocab_size')} · {self.precision}")


def _parameters(config: dict) -> int:
    v, t, d, f, n = (config.get(k, 0) for k in ("vocab_size", "context_length", "d_model", "d_ff", "n_layers"))
    per_block = 2 * 2 * d + d * 3 * d + 3 * d + d * d + d + d * f + f + f * d + d
    return v * d + t * d + n * per_block + 2 * d


def read_header(path: Path) -> dict:
    """The checkpoint's JSON header, without loading any weights."""
    with np.load(path, allow_pickle=False) as archive:
        if "header" not in archive.files:
            raise ValueError("no header (not an AURORA checkpoint)")
        header = archive["header"]
        if header.nbytes > MAX_HEADER_BYTES:
            raise ValueError("header too large")
        names = archive.files
    data = json.loads(str(header))
    if not isinstance(data, dict) or data.get("format") != "aurora-checkpoint":
        raise ValueError("not an AURORA checkpoint")
    data["_has_tokenizer"] = "tokenizer" in names
    data["_has_optimizer"] = any(n.startswith("adam_m/") for n in names)
    return data


def _summary(folder: Path) -> dict:
    path = folder / "summary.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def find_core_models(models_dir: Path | None = None) -> list[CoreModel]:
    root = Path(models_dir or settings.MODELS_DIR)
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.rglob("*.npz")):
        stat = path.stat()
        entry = CoreModel(path, path.relative_to(root).as_posix(), stat.st_size,
                          datetime.fromtimestamp(stat.st_mtime))
        try:
            header = read_header(path)
            entry.config = header.get("config") or {}
            entry.step = int(header.get("step", 0))
            entry.precision = header.get("precision", "float32")
            entry.has_tokenizer = header["_has_tokenizer"]
            entry.has_optimizer = header["_has_optimizer"]
            entry.parameters = _parameters(entry.config)
            entry.summary = _summary(path.parent)
        except Exception as error:   # a damaged or foreign file is listed, not hidden
            entry.readable = False
            entry.problem = str(error) or type(error).__name__
        found.append(entry)
    return found


# ------------------------------------------------------------ training runs


@dataclass
class TrainingRun:
    folder: Path
    name: str
    metrics: list[dict]
    summary: dict

    @property
    def finished(self) -> bool:
        return bool(self.summary)

    @property
    def last_step(self) -> int:
        return self.metrics[-1]["step"] if self.metrics else 0

    @property
    def planned_steps(self) -> int | None:
        config = self.summary.get("train_config") or {}
        return config.get("steps")

    @property
    def best_validation(self) -> float | None:
        values = [m["validation_loss"] for m in self.metrics if "validation_loss" in m]
        return min(values) if values else None


def read_metrics(path: Path) -> list[dict]:
    """Entries of a metrics.jsonl. A half-written last line (training still
    writing) is skipped, never an error."""
    entries = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and "step" in entry:
            entries.append(entry)
    return entries


def find_training_runs(models_dir: Path | None = None) -> list[TrainingRun]:
    root = Path(models_dir or settings.MODELS_DIR)
    if not root.is_dir():
        return []
    runs = []
    for metrics in sorted(root.rglob("metrics.jsonl")):
        folder = metrics.parent
        runs.append(TrainingRun(folder, folder.relative_to(root).as_posix(), read_metrics(metrics), _summary(folder)))
    runs.sort(key=lambda r: r.folder.stat().st_mtime, reverse=True)
    return runs


def training_texts(data_dir: Path | None = None) -> list[Path]:
    root = Path(data_dir or settings.DATA_DIR)
    return sorted(p for p in root.glob("*.txt") if p.is_file()) if root.is_dir() else []


# ---------------------------------------------------------------- commands


def program() -> list[str]:
    """How to run AURORA's own entry point as a new process."""
    if getattr(sys, "frozen", False):
        return [sys.executable]                     # the packaged .exe understands --train
    entry = Path(__file__).resolve().parents[2] / "command_centre.py"
    return [sys.executable, str(entry)]


def training_command(data: Path, out: Path, steps: int, lr: float, vocab_size: int,
                     eval_interval: int | None = None) -> list[str]:
    if steps < 1 or vocab_size < 260 or not lr > 0:
        raise ValueError("steps must be at least 1, the vocabulary at least 260 and the learning rate positive")
    eval_interval = eval_interval or max(1, min(250, steps // 10 or 1))
    return program() + ["--train", "--data", str(data), "--out", str(out), "--steps", str(steps),
                        "--lr", repr(lr), "--vocab-size", str(vocab_size), "--eval-interval", str(eval_interval)]
