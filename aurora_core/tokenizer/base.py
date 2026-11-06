"""
What every AURORA tokenizer has in common: normalisation, special
tokens, id checking, and saving to / loading from a JSON file.

Tokenizer files are plain JSON (never pickle), so loading one can't run
code, and they are checked field by field before use.
"""

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

from aurora_core.tokenizer.normalize import NORMALIZATIONS, normalize
from aurora_core.tokenizer.special import BOS_ID, EOS_ID, SPECIAL_TOKENS

FILE_FORMAT = "aurora-tokenizer"
FILE_VERSION = 1
MAX_FILE_BYTES = 50_000_000


class TokenizerFileError(ValueError):
    """A tokenizer file is missing, malformed or from an unknown format."""


class Tokenizer(ABC):
    kind: str = ""                         # stored in the file as "type"
    _registry: dict[str, type["Tokenizer"]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.kind:
            Tokenizer._registry[cls.kind] = cls

    def __init__(self, normalization: str = "nfc"):
        if normalization not in NORMALIZATIONS:
            raise ValueError(f"normalization must be one of {NORMALIZATIONS}, got {normalization!r}")
        self.normalization = normalization

    # ------------------------------------------------------------ interface

    @property
    @abstractmethod
    def vocab_size(self) -> int: ...

    @abstractmethod
    def _encode(self, text: str) -> list[int]: ...

    @abstractmethod
    def _decode(self, ids: list[int], skip_special: bool) -> str: ...

    @abstractmethod
    def id_to_token(self, token_id: int) -> str: ...

    @abstractmethod
    def _state(self) -> dict: ...

    @classmethod
    @abstractmethod
    def _from_state(cls, state: dict, normalization: str) -> "Tokenizer": ...

    # ------------------------------------------------------- encode / decode

    def normalize(self, text: str) -> str:
        return normalize(text, self.normalization)

    def encode(self, text: str, *, add_bos: bool = False, add_eos: bool = False) -> list[int]:
        """Text -> token ids. Special tokens come only from add_bos / add_eos."""
        ids = self._encode(self.normalize(text))
        if add_bos:
            ids.insert(0, BOS_ID)
        if add_eos:
            ids.append(EOS_ID)
        return ids

    def decode(self, ids, *, skip_special: bool = True) -> str:
        """Token ids -> text. With skip_special, <pad>/<bos>/<eos> are left
        out; otherwise they appear by name, e.g. "<eos>"."""
        return self._decode(self._check_ids(ids), skip_special)

    def _check_ids(self, ids) -> list[int]:
        if isinstance(ids, (str, bytes)):
            raise TypeError("decode expects a sequence of token ids, not text")
        checked = []
        for token_id in np.asarray(ids).reshape(-1).tolist() if isinstance(ids, np.ndarray) else ids:
            if isinstance(token_id, bool) or not isinstance(token_id, (int, np.integer)):
                raise ValueError(f"token ids must be integers, got {token_id!r}")
            token_id = int(token_id)
            if not 0 <= token_id < self.vocab_size:
                raise ValueError(f"token id {token_id} is outside the vocabulary (0-{self.vocab_size - 1})")
            checked.append(token_id)
        return checked

    def tokens(self, text: str) -> list[str]:
        """The readable form of each token `text` is split into."""
        return [self.id_to_token(i) for i in self.encode(text)]

    # ---------------------------------------------------------- persistence

    def save(self, path) -> None:
        """Write the tokenizer to a JSON file (written to a temporary file
        first, so an interrupted save never leaves a half-written file)."""
        data = {
            "format": FILE_FORMAT,
            "version": FILE_VERSION,
            "type": self.kind,
            "normalization": self.normalization,
            "special_tokens": list(SPECIAL_TOKENS),
            **self._state(),
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def load(path) -> "Tokenizer":
        """Read a tokenizer saved by save(), whatever its type."""
        path = Path(path)
        if not path.is_file():
            raise TokenizerFileError(f"no tokenizer file at {path}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise TokenizerFileError("tokenizer file is too large")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TokenizerFileError(f"tokenizer file is not valid JSON: {error}") from None
        if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
            raise TokenizerFileError("not an AURORA tokenizer file")
        if data.get("version") != FILE_VERSION:
            raise TokenizerFileError(f"unsupported tokenizer file version {data.get('version')!r}")
        if data.get("special_tokens") != list(SPECIAL_TOKENS):
            raise TokenizerFileError("special tokens differ from this version of AURORA")
        cls = Tokenizer._registry.get(data.get("type"))
        if cls is None:
            raise TokenizerFileError(f"unknown tokenizer type {data.get('type')!r}")
        normalization = data.get("normalization")
        if normalization not in NORMALIZATIONS:
            raise TokenizerFileError(f"unknown normalization {normalization!r}")
        try:
            return cls._from_state(data, normalization)
        except (TypeError, ValueError) as error:
            raise TokenizerFileError(f"invalid tokenizer data: {error}") from None
