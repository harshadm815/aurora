"""
Training data: reading text, splitting it, and cutting token sequences
into (input, target) pairs.

The text is split into training, validation and test parts *before*
anything is learned from it (including the tokenizer), and the parts are
contiguous blocks, so no sentence appears in more than one of them.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

_GUTENBERG_START = "*** START OF THE PROJECT GUTENBERG EBOOK"
_GUTENBERG_END = "*** END OF THE PROJECT GUTENBERG EBOOK"


def read_text(path) -> str:
    """Read a UTF-8 text file (a byte-order mark, if any, is dropped)."""
    return Path(path).read_text(encoding="utf-8-sig")


def strip_gutenberg(text: str) -> str:
    """Keep only the book from a Project Gutenberg file, without its
    licence header and footer. Other text is returned unchanged."""
    start = text.find(_GUTENBERG_START)
    end = text.find(_GUTENBERG_END)
    if start == -1 or end == -1 or end < start:
        return text
    start = text.find("\n", start)
    return text[start + 1:end].strip() + "\n"


@dataclass(frozen=True)
class TextSplits:
    train: str
    validation: str
    test: str


def split_text(text: str, validation: float = 0.05, test: float = 0.05) -> TextSplits:
    """Split text into contiguous train / validation / test parts.

    The cut points are moved forward to the next blank line (paragraph
    break) when there is one nearby, so no paragraph is cut in half.
    train + validation + test always equals the original text.
    """
    if not (0 < validation < 1 and 0 < test < 1 and validation + test < 1):
        raise ValueError("validation and test must be fractions that leave room for training")
    n = len(text)

    def cut(position: int) -> int:
        nearby = text.find("\n\n", position, position + max(2000, n // 100))
        return nearby + 2 if nearby != -1 else position

    a = cut(int(n * (1 - validation - test)))
    b = max(a, cut(int(n * (1 - test))))
    splits = TextSplits(text[:a], text[a:b], text[b:])
    if not (splits.train and splits.validation and splits.test):
        raise ValueError("text is too short to split into three parts")
    return splits


class TokenDataset:
    """A sequence of token ids, served as (inputs, targets) windows where
    targets are the inputs shifted one token to the left: at every
    position the model learns to predict the next token."""

    def __init__(self, ids, context_length: int):
        ids = np.asarray(ids, dtype=np.int64)
        if ids.ndim != 1:
            raise ValueError("ids must be a 1-D sequence")
        if len(ids) < context_length + 1:
            raise ValueError(
                f"need at least {context_length + 1} tokens for a context of {context_length}, got {len(ids)}"
            )
        self.ids = ids
        self.context_length = context_length

    def __len__(self) -> int:
        return len(self.ids)

    def random_batch(self, rng: np.random.Generator, batch_size: int) -> tuple[np.ndarray, np.ndarray]:
        """batch_size windows starting at random positions: (B, T) each."""
        t = self.context_length
        starts = rng.integers(0, len(self.ids) - t, size=batch_size)
        offsets = np.arange(t)
        inputs = self.ids[starts[:, None] + offsets]
        targets = self.ids[starts[:, None] + offsets + 1]
        return inputs, targets

    def windows(self, max_windows: int | None = None) -> tuple[np.ndarray, np.ndarray]:
        """Non-overlapping windows covering the data in order, for
        evaluation (the same every time). With max_windows, an evenly
        spaced subset."""
        t = self.context_length
        starts = np.arange(0, len(self.ids) - t, t)
        if max_windows is not None and len(starts) > max_windows:
            starts = starts[np.linspace(0, len(starts) - 1, max_windows).round().astype(int)]
        offsets = np.arange(t)
        return self.ids[starts[:, None] + offsets], self.ids[starts[:, None] + offsets + 1]
