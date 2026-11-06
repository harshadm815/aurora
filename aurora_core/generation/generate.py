"""
Text generation with AURORA-T0: repeatedly score the next token, pick
one, append it and continue.

When the text grows longer than the model's context, only the most
recent context_length tokens are given to the model (a sliding window),
so generation can run for any length. Each new token needs a full
forward pass over the window; caching keys and values to avoid that is
part of the runtime (Phase 6).
"""

import codecs
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass

import numpy as np

from aurora_core.generation.sampling import sample_next
from aurora_core.tokenizer import BOS_ID, EOS_ID, PAD_ID, UNK_ID, Tokenizer
from aurora_core.transformer import AuroraT0


@dataclass(frozen=True)
class GenerationConfig:
    max_new_tokens: int = 100
    temperature: float = 0.8
    top_k: int | None = 40
    top_p: float | None = None
    seed: int | None = None
    stop_at_eos: bool = True

    def __post_init__(self):
        if self.max_new_tokens < 0:
            raise ValueError("max_new_tokens must be 0 or more")
        if self.temperature < 0:
            raise ValueError("temperature must be 0 or more")
        if self.top_k is not None and self.top_k < 1:
            raise ValueError("top_k must be at least 1")
        if self.top_p is not None and not 0 < self.top_p <= 1:
            raise ValueError("top_p must be in (0, 1]")

    def to_dict(self) -> dict:
        return asdict(self)


# Tokens that only mark structure; the model must never write them as text.
BANNED = (PAD_ID, UNK_ID, BOS_ID)


def stream_ids(model: AuroraT0, prompt_ids, config: GenerationConfig = GenerationConfig()) -> Iterator[int]:
    """Yield new token ids one at a time after `prompt_ids`."""
    ids = [int(i) for i in prompt_ids]
    if not ids:
        raise ValueError("the prompt must contain at least one token")
    rng = np.random.default_rng(config.seed)
    context = model.config.context_length
    for _ in range(config.max_new_tokens):
        window = np.array(ids[-context:], dtype=np.int64)
        logits = model(window)[-1]
        token = sample_next(logits, rng, temperature=config.temperature, top_k=config.top_k,
                            top_p=config.top_p, banned=BANNED)
        if token == EOS_ID and config.stop_at_eos:
            return
        ids.append(token)
        yield token


def generate_ids(model: AuroraT0, prompt_ids, config: GenerationConfig = GenerationConfig()) -> list[int]:
    return list(stream_ids(model, prompt_ids, config))


class TextStream:
    """Turns token ids, added one at a time, into text pieces.

    Each token is decoded once, on its own: byte-level tokens go through
    Python's incremental UTF-8 decoder, which holds back a character until
    all its bytes have arrived (so a multi-byte character split across
    tokens is never shown half-finished). The joined pieces always equal
    tokenizer.decode(all ids), but the cost per token stays constant
    instead of growing with the length of the text.
    """

    def __init__(self, tokenizer: Tokenizer):
        self.tokenizer = tokenizer
        self.ids: list[int] = []
        self._parts: list[str] = []
        self._bytes = hasattr(tokenizer, "token_bytes")
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def add(self, token: int) -> str:
        """Add a token; return the new text that can be shown now (maybe "")."""
        token = int(token)
        if not 0 <= token < self.tokenizer.vocab_size:
            raise ValueError(f"token id {token} is outside the vocabulary")
        self.ids.append(token)
        if not self._bytes:
            piece = self.tokenizer.decode([token])
        elif token == UNK_ID:
            # Like decode(): an unknown token ends any unfinished character.
            piece = self._decoder.decode(b"", final=True) + "�"
        elif token in (PAD_ID, BOS_ID, EOS_ID):
            piece = ""
        else:
            piece = self._decoder.decode(self.tokenizer.token_bytes(token))
        if piece:
            self._parts.append(piece)
        return piece

    def finish(self) -> str:
        """Whatever is still held back (bytes that never became a character)."""
        piece = self._decoder.decode(b"", final=True) if self._bytes else ""
        if piece:
            self._parts.append(piece)
        return piece

    @property
    def text(self) -> str:
        """The text so far, including anything still held back."""
        held = self._decoder.getstate()[0] if self._bytes else b""
        shown = "".join(self._parts)
        return shown + held.decode("utf-8", errors="replace") if held else shown


def generate_text(
    model: AuroraT0,
    tokenizer: Tokenizer,
    prompt: str,
    config: GenerationConfig = GenerationConfig(),
    on_text: Callable[[str], None] | None = None,
) -> str:
    """The continuation of `prompt` (without the prompt itself).

    on_text, if given, receives the text as it is produced (see TextStream).
    """
    if tokenizer.vocab_size != model.config.vocab_size:
        raise ValueError("tokenizer and model have different vocabulary sizes")
    stream = TextStream(tokenizer)
    for token in stream_ids(model, tokenizer.encode(prompt), config):
        piece = stream.add(token)
        if piece and on_text:
            on_text(piece)
    rest = stream.finish()
    if rest and on_text:
        on_text(rest)
    return stream.text
