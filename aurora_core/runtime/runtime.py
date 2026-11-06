"""
AuroraRuntime: loads an AURORA model and generates text with it, with
no other model runner involved.

    runtime = AuroraRuntime()
    runtime.load("Models/aurora_t0_lr1e-2/best.npz")
    result = runtime.generate("Sherlock Holmes", GenerationConfig(temperature=0.5))
    result.text, result.stats

Resource management:
  - limits on model file size, tokens per request and seconds per request;
  - one generation at a time per runtime (a second concurrent request is
    refused at once rather than queued);
  - a request can be cancelled from another thread with a threading.Event;
  - memory_usage() reports the weights and the KV cache; unload() frees
    them.
"""

import threading
import time
from dataclasses import asdict, dataclass

import numpy as np

from aurora_core.generation.generate import BANNED, GenerationConfig, TextStream
from aurora_core.generation.sampling import sample_next
from aurora_core.runtime.loader import ModelInfo, load_model
from aurora_core.runtime.session import InferenceSession
from aurora_core.tokenizer import EOS_ID


class RuntimeNotReady(RuntimeError):
    """No model is loaded."""


class RuntimeBusy(RuntimeError):
    """The runtime is already generating."""


@dataclass(frozen=True)
class RuntimeLimits:
    max_model_bytes: int = 500_000_000
    max_new_tokens: int = 2048
    max_seconds: float = 120.0
    # Tokens kept when the context window is full (see session.py);
    # None = half the context length.
    keep_on_overflow: int | None = None

    def __post_init__(self):
        if self.max_model_bytes < 1 or self.max_new_tokens < 1 or self.max_seconds <= 0:
            raise ValueError("runtime limits must be positive")


@dataclass
class GenerationStats:
    prompt_tokens: int
    prompt_truncated: int
    new_tokens: int
    stop_reason: str            # "length", "eos", "cancelled" or "timeout"
    prefill_seconds: float
    generation_seconds: float
    tokens_per_second: float
    cache_rebuilds: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GenerationResult:
    text: str
    token_ids: list[int]
    stats: GenerationStats


class AuroraRuntime:
    def __init__(self, limits: RuntimeLimits = RuntimeLimits()):
        self.limits = limits
        self._model = None
        self._tokenizer = None
        self._info: ModelInfo | None = None
        self._session: InferenceSession | None = None
        self._busy = threading.Lock()
        self.clock = time.perf_counter            # replaceable in tests

    # ------------------------------------------------------------- loading

    def load(self, path) -> ModelInfo:
        with self._exclusive():
            model, tokenizer, info = load_model(path, self.limits.max_model_bytes)
            session = InferenceSession(model, keep=self.limits.keep_on_overflow)
            self._model, self._tokenizer, self._info, self._session = model, tokenizer, info, session
            return info

    def unload(self) -> None:
        with self._exclusive():
            self._model = self._tokenizer = self._info = self._session = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def info(self) -> ModelInfo:
        self._require()
        return self._info

    @property
    def tokenizer(self):
        self._require()
        return self._tokenizer

    def memory_usage(self) -> dict:
        if not self.loaded:
            return {"parameters": 0, "kv_cache": 0, "total": 0}
        weights = self._info.parameter_bytes
        cache = self._session.cache.nbytes
        return {"parameters": weights, "kv_cache": cache, "total": weights + cache}

    # ---------------------------------------------------------- generation

    def generate(self, prompt: str, config: GenerationConfig = GenerationConfig(),
                 on_text=None, cancel: threading.Event | None = None) -> GenerationResult:
        """Continue `prompt`. on_text(piece) receives text as it is made."""
        with self._exclusive():
            self._require()
            if config.max_new_tokens > self.limits.max_new_tokens:
                raise ValueError(f"at most {self.limits.max_new_tokens} new tokens per request")
            prompt_ids = self._tokenizer.encode(prompt)
            if not prompt_ids:
                raise ValueError("the prompt is empty")
            session, rng = self._session, np.random.default_rng(config.seed)
            stream = TextStream(self._tokenizer)

            started = self.clock()
            session.prefills = 0
            logits = session.start(prompt_ids)
            prefilled = self.clock()
            reason = "length"
            for _ in range(config.max_new_tokens):
                if cancel is not None and cancel.is_set():
                    reason = "cancelled"
                    break
                if self.clock() - started > self.limits.max_seconds:
                    reason = "timeout"
                    break
                token = sample_next(logits, rng, temperature=config.temperature, top_k=config.top_k,
                                    top_p=config.top_p, banned=BANNED)
                if token == EOS_ID and config.stop_at_eos:
                    reason = "eos"
                    break
                piece = stream.add(token)
                if piece and on_text:
                    on_text(piece)
                if len(stream.ids) < config.max_new_tokens:
                    logits = session.append(token)
            rest = stream.finish()
            if rest and on_text:
                on_text(rest)
            finished = self.clock()

            generation_seconds = finished - prefilled
            stats = GenerationStats(
                prompt_tokens=len(prompt_ids),
                prompt_truncated=session.truncated,
                new_tokens=len(stream.ids),
                stop_reason=reason,
                prefill_seconds=prefilled - started,
                generation_seconds=generation_seconds,
                tokens_per_second=len(stream.ids) / generation_seconds if generation_seconds > 0 else 0.0,
                cache_rebuilds=session.prefills - 1,
            )
            return GenerationResult(stream.text, list(stream.ids), stats)

    # ------------------------------------------------------------- helpers

    def _require(self) -> None:
        if not self.loaded:
            raise RuntimeNotReady("no model is loaded; call load() first")

    def _exclusive(self):
        runtime = self

        class _Guard:
            def __enter__(self):
                if not runtime._busy.acquire(blocking=False):
                    raise RuntimeBusy("the runtime is already busy with another request")

            def __exit__(self, *exc):
                runtime._busy.release()
                return False

        return _Guard()
