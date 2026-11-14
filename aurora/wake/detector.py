"""
Recognising the wake phrase ("wake up") in the audio captured while the
activation key is held.

The speech model (Vosk, the same one AURORA listens with) is loaded only
when it's first needed: 7 s the first time from the USB drive, ~1.5 s
once Windows has it cached, +135 MB. So:

  - audio is kept in memory from the moment the key goes down and handed
    to the recogniser as soon as the model is ready, so nothing said
    while it loads is lost;
  - the model then stays loaded for WAKE_MODEL_KEEP_MINUTES so the next
    wake is quick, and is freed after that.

The recogniser is limited to the wake phrase, a few sound-alikes ("what's
up", "wait up"…) and "[unk]" (anything else), so other speech has
somewhere to go other than the phrase. Audio is
never written to disk and nothing heard is logged.
"""

import json
import logging
import queue
import threading
import time

from aurora.config import settings

logger = logging.getLogger(__name__)

NOT_HEARD, HEARD, UNKNOWN_WORDS = "not_heard", "heard", "unknown_words"


def phrase_words(phrase: str) -> list[str]:
    return [w for w in "".join(c if c.isalpha() or c in " '" else " " for c in phrase.lower()).split() if w]


def unknown_words(phrase: str, model) -> list[str]:
    """Words of the phrase the speech model doesn't know (it couldn't ever hear them)."""
    find = getattr(model, "vosk_model_find_word", None) or model.find_word
    return [w for w in phrase_words(phrase) if find(w) < 0]


# Phrases that sound like common wake phrases. Listed in the recogniser's grammar, they give
# "what's up" somewhere better to go than "wake up" (measured: 1 in 10 → see the tests).
SOUND_ALIKES = ["what's up", "wait up", "make up", "take up", "shake up", "wake me", "break up", "wake",
                "up", "hey", "aurora", "hello"]          # parts of the phrase itself are left out (decoys())


def decoys(phrase: str, model) -> list[str]:
    """Sound-alikes the model knows, other than the phrase or a part of it ("wake" or "up" alone
    would let a short pause inside "wake up" turn it into two other results)."""
    return [d for d in SOUND_ALIKES if f" {d} " not in f" {phrase} " and not unknown_words(d, model)]


def _load_vosk():
    from vosk import Model, SetLogLevel

    SetLogLevel(-1)
    return Model(str(settings.VOICE_MODEL_DIR))


class ModelCache:
    """Loads the speech model on first use (in the background) and frees it after a while unused."""

    def __init__(self, loader=_load_vosk, keep_seconds: float | None = None, clock=time.monotonic):
        self.loader = loader
        self.keep_seconds = (settings.WAKE_MODEL_KEEP_MINUTES * 60) if keep_seconds is None else keep_seconds
        self.clock = clock
        self._model = None
        self._used = 0.0
        self._lock = threading.Lock()

    def get(self):
        """The model (loading it now if needed; call from a worker thread)."""
        with self._lock:
            if self._model is None:
                started = self.clock()
                self._model = self.loader()
                logger.info("Wake: speech model loaded in %.1f s", self.clock() - started)
            self._used = self.clock()
            return self._model

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def release_if_idle(self) -> bool:
        """Frees the model if it hasn't been used for keep_seconds. True if it was freed."""
        with self._lock:
            if self._model is not None and self.clock() - self._used >= self.keep_seconds:
                self._model = None
                logger.info("Wake: speech model freed (unused)")
                return True
        return False


class WakeSession:
    """One key hold: ``feed()`` audio while the key is held, ``finish()`` on release.
    ``on_result(HEARD | NOT_HEARD)`` is called once, from the worker thread."""

    def __init__(self, cache: ModelCache, phrase: str, rate: int, on_result, max_wait: float = 10.0):
        self.cache, self.phrase, self.rate, self.on_result = cache, " ".join(phrase_words(phrase)), rate, on_result
        self.max_wait = max_wait
        self._audio: queue.Queue = queue.Queue()
        self._done = threading.Event()
        self.result: str | None = None
        self.unknown: list[str] = []
        self._previous = ""                  # the last recognised result (a phrase split by a pause)
        self._thread = threading.Thread(target=self._run, name="aurora-wake-detect", daemon=True)
        self._thread.start()

    def feed(self, chunk: bytes) -> None:
        if not self._done.is_set():
            self._audio.put(chunk)

    def finish(self) -> None:
        """The key was released: the microphone is closed; check what was captured, then end."""
        self._audio.put(None)

    def cancel(self) -> None:
        """Another key was pressed: drop everything without a result."""
        self._done.set()
        self._audio.put(None)

    def _matches(self, result_json: str) -> bool:
        """The phrase in one result, or split across this result and the previous one by a short pause."""
        text = json.loads(result_json).get("text", "").strip()
        if not text:
            return False
        joined = f"{self._previous} {text}".strip()
        self._previous = text
        return text == self.phrase or joined == self.phrase

    def _report(self, result: str) -> None:
        if self.result is None:
            self.result = result
            self._done.set()
            self.on_result(result)

    def _run(self) -> None:
        try:
            model = self.cache.get()
            unknown = unknown_words(self.phrase, model)
            if unknown:
                self.unknown = unknown
                self._report(UNKNOWN_WORDS)
                return
            from vosk import KaldiRecognizer
            grammar = [self.phrase, *decoys(self.phrase, model), "[unk]"]
            recogniser = KaldiRecognizer(model, self.rate, json.dumps(grammar))
        except Exception:
            logger.exception("Wake: the speech model couldn't be loaded")
            if not self._done.is_set():
                self._report(NOT_HEARD)
            return
        self._listen(recogniser)

    def _listen(self, recogniser) -> None:
        deadline = time.monotonic() + self.max_wait
        while not self._done.is_set():
            try:
                chunk = self._audio.get(timeout=0.2)
            except queue.Empty:
                if time.monotonic() > deadline:
                    break
                continue
            if chunk is None:
                if not self._done.is_set() and self._matches(recogniser.FinalResult()):
                    self._report(HEARD)
                break
            if recogniser.AcceptWaveform(chunk) and self._matches(recogniser.Result()):
                self._report(HEARD)
                return
        if not self._done.is_set():
            self._report(NOT_HEARD)
