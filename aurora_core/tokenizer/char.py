"""
Character-level tokenizer: one token per character.

The simplest tokenizer, for first experiments. Its vocabulary is the set
of characters seen in training; any other character becomes <unk>, so
it is lossless only for text made of known characters. Use
ByteBPETokenizer when every possible input must survive.
"""

from collections import Counter
from collections.abc import Iterable

from aurora_core.tokenizer.base import Tokenizer
from aurora_core.tokenizer.special import NUM_SPECIAL, SPECIAL_TOKENS, UNK, UNK_ID

REPLACEMENT_CHARACTER = "�"   # how <unk> appears in decoded text: �


class CharTokenizer(Tokenizer):
    kind = "char"

    def __init__(self, characters: Iterable[str], normalization: str = "nfc"):
        super().__init__(normalization)
        chars = list(characters)
        for c in chars:
            if not isinstance(c, str) or len(c) != 1:
                raise ValueError(f"each vocabulary entry must be one character, got {c!r}")
        if len(set(chars)) != len(chars):
            raise ValueError("vocabulary characters must be unique")
        self._chars = chars
        self._index = {c: NUM_SPECIAL + i for i, c in enumerate(chars)}

    @classmethod
    def train(
        cls,
        texts: Iterable[str],
        *,
        max_vocab: int | None = None,
        min_frequency: int = 1,
        normalization: str = "nfc",
    ) -> "CharTokenizer":
        """Build the vocabulary from `texts`: most frequent characters first
        (ties in code-point order), keeping those seen at least
        `min_frequency` times, up to `max_vocab` tokens including the
        special tokens."""
        if max_vocab is not None and max_vocab <= NUM_SPECIAL:
            raise ValueError(f"max_vocab must be more than the {NUM_SPECIAL} special tokens")
        if min_frequency < 1:
            raise ValueError("min_frequency must be at least 1")
        helper = cls([], normalization)
        counts: Counter[str] = Counter()
        for text in texts:
            counts.update(helper.normalize(text))
        ranked = sorted((c for c, n in counts.items() if n >= min_frequency), key=lambda c: (-counts[c], c))
        if max_vocab is not None:
            ranked = ranked[: max_vocab - NUM_SPECIAL]
        return cls(ranked, normalization)

    @property
    def vocab_size(self) -> int:
        return NUM_SPECIAL + len(self._chars)

    def _encode(self, text: str) -> list[int]:
        return [self._index.get(c, UNK_ID) for c in text]

    def _decode(self, ids: list[int], skip_special: bool) -> str:
        parts = []
        for i in ids:
            if i >= NUM_SPECIAL:
                parts.append(self._chars[i - NUM_SPECIAL])
            elif i == UNK_ID:
                parts.append(REPLACEMENT_CHARACTER if skip_special else UNK)
            elif not skip_special:
                parts.append(SPECIAL_TOKENS[i])
        return "".join(parts)

    def id_to_token(self, token_id: int) -> str:
        token_id = self._check_ids([token_id])[0]
        return SPECIAL_TOKENS[token_id] if token_id < NUM_SPECIAL else self._chars[token_id - NUM_SPECIAL]

    def token_to_id(self, token: str) -> int:
        if token in SPECIAL_TOKENS:
            return SPECIAL_TOKENS.index(token)
        if token not in self._index:
            raise KeyError(f"{token!r} is not in the vocabulary")
        return self._index[token]

    def _state(self) -> dict:
        return {"characters": self._chars}

    @classmethod
    def _from_state(cls, state: dict, normalization: str) -> "CharTokenizer":
        chars = state.get("characters")
        if not isinstance(chars, list):
            raise ValueError("'characters' must be a list")
        return cls(chars, normalization)
