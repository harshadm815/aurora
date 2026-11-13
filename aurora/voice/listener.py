"""
Speech-to-text and wake-word detection with Vosk (offline).

Audio comes from an ``AudioSource``: the microphone, or a WAV file in
tests. Audio is processed in memory only; it is never saved, and
transcripts are never logged.

Wake word: a second Vosk recogniser restricted to a tiny grammar
("aurora", "hey aurora", …) listens continuously. It is cheap and rarely
fires on other speech. When it hears the wake word, the same audio is
transcribed in full, so "Aurora, what's the weather?" said in one breath
works as well as "Aurora" … pause … "what's the weather?".
"""

import json
import logging
import queue
import re
import time
import wave
from collections.abc import Iterator
from pathlib import Path

from aurora.config import settings
from aurora.voice.vad import VoiceActivityDetector

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_FRAMES = 4000  # 0.25 s at 16 kHz

WAKE_GRAMMAR = ["aurora", "hey aurora", "okay aurora", "ok aurora", "hi aurora", "[unk]"]

# How the full recogniser tends to spell the wake word.
_WAKE_PREFIX = re.compile(
    r"^\s*(?:(?:hey|hi|ok|okay)\s+)?(?:aurora|or\s+aura|or\s+aurora|a\s+aura|aura|or\s+roar\w*)\b[\s,]*",
    re.IGNORECASE,
)


class VoiceUnavailable(RuntimeError):
    """Voice can't be used (package or model missing, no microphone)."""


# --- Audio sources ------------------------------------------------------------

class AudioSource:
    """Yields raw 16-bit mono PCM chunks at ``rate``."""

    rate: int = SAMPLE_RATE

    def chunks(self) -> Iterator[bytes]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class MicrophoneSource(AudioSource):
    """The default microphone. Open only while AURORA is listening."""

    def __init__(self, device=None):
        try:
            import sounddevice
        except ImportError as error:
            raise VoiceUnavailable("The 'sounddevice' package isn't installed.") from error

        self.rate = SAMPLE_RATE
        self._queue: queue.Queue[bytes] = queue.Queue()
        try:
            self._stream = sounddevice.RawInputStream(
                samplerate=self.rate, blocksize=CHUNK_FRAMES, dtype="int16",
                channels=1, device=device if device is not None else settings.VOICE_INPUT_DEVICE,
                callback=lambda data, frames, time_info, status: self._queue.put(bytes(data)),
            )
            self._stream.start()
        except Exception as error:
            raise VoiceUnavailable(f"I can't open the microphone ({error}).") from error

    def chunks(self) -> Iterator[bytes]:
        while True:
            try:
                yield self._queue.get(timeout=1)
            except queue.Empty:
                yield b""   # Lets callers check their timeouts.

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class WavSource(AudioSource):
    """A WAV file (16-bit mono), followed by silence so speech can end."""

    def __init__(self, path: str | Path, trailing_silence: float = 1.5):
        with wave.open(str(path), "rb") as file:
            if file.getsampwidth() != 2 or file.getnchannels() != 1:
                raise ValueError("WAV must be 16-bit mono.")
            self.rate = file.getframerate()
            self._data = file.readframes(file.getnframes())
        self._data += b"\x00\x00" * int(self.rate * trailing_silence)

    def chunks(self) -> Iterator[bytes]:
        step = CHUNK_FRAMES * 2
        for start in range(0, len(self._data), step):
            yield self._data[start:start + step]


# --- Text helpers -------------------------------------------------------------

def apply_corrections(text: str, corrections: dict[str, str] | None = None) -> str:
    """
    Tidies a transcript: fixes words the small model mishears (e.g.
    {"pony": "Pune"} from settings) and turns spoken arithmetic into digits.
    """
    corrections = settings.VOICE_CORRECTIONS if corrections is None else corrections
    for heard, meant in corrections.items():
        text = re.sub(rf"\b{re.escape(heard)}\b", meant, text, flags=re.IGNORECASE)
    return numbers_to_digits(text)


def strip_wake_word(text: str) -> str:
    return _WAKE_PREFIX.sub("", text, count=1).strip()


_UNITS = {
    w: i for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen".split()
    )
}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"thousand": 1_000, "million": 1_000_000}
_ARITHMETIC_WORD = re.compile(
    r"\b(?:plus|minus|times|multiplied|divided|over|squared|cubed|power|percent|calculate|add|subtract|"
    # Statistics ("the average of four eight and fifteen")
    r"average|mean|median|mode|sum|total|range|minimum|maximum|smallest|largest|biggest|lowest|highest|"
    r"deviation)\b",
    re.IGNORECASE,
)


def _continues(run: list[str], word: str) -> bool:
    """Whether ``word`` belongs to the number being read ("thirty five" = 35)
    or starts a new one ("four eight" = 4 and 8)."""
    if not run:
        return True
    previous = run[-1]
    if "point" in run:                                   # decimals: "three point one four"
        return word in _UNITS and _UNITS[word] < 10
    if word in ("point", "hundred") or word in _SCALES:
        return True
    if previous == "hundred" or previous in _SCALES:
        return True
    if previous in _TENS and word in _UNITS and 0 < _UNITS[word] < 10:
        return True
    return False


def _parse_number(words: list[str]) -> str:
    total, current, decimals, after_point = 0, 0, "", False
    for word in words:
        if after_point:
            decimals += str(_UNITS[word])
        elif word == "point":
            after_point = True
        elif word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word in _SCALES:
            total += (current or 1) * _SCALES[word]
            current = 0
    number = str(total + current)
    return f"{number}.{decimals}" if decimals else number


def numbers_to_digits(text: str) -> str:
    """
    "what is twelve times seven" -> "what is 12 times 7".

    Only used when the sentence contains an arithmetic word, so ordinary
    speech ("which one is it") is left alone.
    """
    # The small model often hears "times" as "time": "twelve time seven".
    number = r"(?:\d+|" + "|".join(list(_UNITS) + list(_TENS)) + r")"
    text = re.sub(rf"\b({number})\s+time\s+({number})\b", r"\1 times \2", text, flags=re.IGNORECASE)

    if not _ARITHMETIC_WORD.search(text):
        return text

    words = text.split()
    out: list[str] = []
    run: list[str] = []

    def is_number_word(index: int) -> bool:
        word = words[index].lower()
        if word in _UNITS or word in _TENS or word in _SCALES or word == "hundred":
            return True
        if word == "point":
            return bool(run) and index + 1 < len(words) and words[index + 1].lower() in _UNITS
        # "one hundred and twenty": "and" inside a number.
        if word == "and" and run and run[-1] in ("hundred", "thousand", "million"):
            nxt = words[index + 1].lower() if index + 1 < len(words) else ""
            return nxt in _UNITS or nxt in _TENS
        return False

    for index, word in enumerate(words):
        if is_number_word(index):
            lowered = word.lower()
            if lowered != "and":
                if not _continues(run, lowered):
                    out.append(_parse_number(run))
                    run = []
                run.append(lowered)
            continue
        if run:
            out.append(_parse_number(run))
            run = []
        out.append(word)
    if run:
        out.append(_parse_number(run))

    return " ".join(out)


# --- Listener -----------------------------------------------------------------

class Listener:
    """The Vosk recogniser (a provider: see providers.py)."""

    name = "vosk"
    local = True

    def __init__(self, model_path: str | Path | None = None):
        try:
            from vosk import Model, SetLogLevel
        except ImportError as error:
            raise VoiceUnavailable("The 'vosk' package isn't installed.") from error

        path = Path(model_path or settings.VOICE_MODEL_DIR)
        if not (path / "am").is_dir():
            raise VoiceUnavailable(
                f"The speech model isn't in {path}. Download vosk-model-small-en-us-0.15 "
                "from https://alphacephei.com/vosk/models and unzip it there."
            )

        SetLogLevel(-1)
        self.model = Model(str(path))

    def _recognizer(self, rate: int, grammar: list[str] | None = None):
        from vosk import KaldiRecognizer

        if grammar is None:
            return KaldiRecognizer(self.model, rate)
        return KaldiRecognizer(self.model, rate, json.dumps(grammar))

    @staticmethod
    def _text(result_json: str) -> str:
        return json.loads(result_json).get("text", "").strip()

    def transcribe(self, audio: bytes, rate: int) -> str:
        recognizer = self._recognizer(rate)
        recognizer.AcceptWaveform(audio)
        return apply_corrections(self._text(recognizer.FinalResult()))

    def listen_once(self, source: AudioSource, max_seconds: float | None = None, on_level=None,
                    should_stop=lambda: False, on_partial=None) -> str:
        """
        One utterance. Ends when the voice activity detector hears the
        speaker stop, when the recogniser finishes a sentence, after
        ``max_seconds``, or when ``should_stop()`` is true. Returns "" if
        nobody spoke (the detector gives up after VOICE_NO_SPEECH_SECONDS
        instead of waiting the full time).

        ``on_level(rms, speaking)`` gets each chunk's loudness, for a level meter.
        ``on_partial(text)`` gets the words recognised so far whenever they
        change, so they can be shown while you speak (never logged).
        """
        max_seconds = settings.VOICE_LISTEN_SECONDS if max_seconds is None else max_seconds
        recognizer = self._recognizer(source.rate)
        detector = VoiceActivityDetector(source.rate)
        live = isinstance(source, MicrophoneSource)
        deadline = time.monotonic() + max_seconds
        heard = 0.0
        shown = ""

        for chunk in source.chunks():
            if should_stop():
                break
            detector.feed(chunk)
            if on_level is not None:
                on_level(detector.level, detector.started and not detector.ended)
            if chunk and recognizer.AcceptWaveform(chunk):
                text = self._text(recognizer.Result())
                if text:
                    return apply_corrections(text)
            elif chunk and on_partial is not None:
                partial = json.loads(recognizer.PartialResult()).get("partial", "").strip()
                if partial != shown:
                    shown = partial
                    on_partial(partial)
            if detector.ended or detector.gave_up:
                break
            heard += len(chunk) / 2 / source.rate
            # The microphone is limited by the clock, a file by its length.
            if (live and time.monotonic() > deadline) or (not live and heard >= max_seconds):
                break

        if not detector.started:
            return ""                 # only background noise: don't transcribe it into words
        return apply_corrections(self._text(recognizer.FinalResult()))

    def wait_for_wake_word(self, source: AudioSource, should_stop=lambda: False) -> str | None:
        """
        Listens until the wake word is heard. Returns whatever was said
        after it in the same breath ("" if only the wake word), or None if
        the audio ended or ``should_stop()`` became true.
        """
        wake = self._recognizer(source.rate, WAKE_GRAMMAR)
        utterance: list[bytes] = []
        max_bytes = int(source.rate * 2 * settings.VOICE_LISTEN_SECONDS)
        chunks = source.chunks()

        for chunk in chunks:
            if should_stop():
                return None
            if not chunk:
                continue

            utterance.append(chunk)
            if sum(len(c) for c in utterance) > max_bytes:
                utterance.pop(0)

            if wake.AcceptWaveform(chunk):
                heard = self._text(wake.Result())
                if "aurora" in heard.split():
                    logger.info("Wake word heard")
                    return strip_wake_word(self._finish_request(utterance, chunks, source.rate))
                utterance.clear()

        return None

    def _finish_request(self, utterance: list[bytes], chunks: Iterator[bytes], rate: int) -> str:
        """
        The wake-word recogniser often ends an utterance at a short pause
        ("Aurora, what is twelve | times seven"). Transcribe what was heard
        so far with the full recogniser and keep listening until the
        speaker really stops (no new words for VOICE_END_SILENCE seconds).
        """
        full = self._recognizer(rate)
        texts: list[str] = []

        for chunk in utterance:
            if full.AcceptWaveform(chunk):
                texts.append(self._text(full.Result()))

        quiet, heard = 0.0, 0.0
        last_partial = json.loads(full.PartialResult()).get("partial", "")
        for chunk in chunks:
            duration = len(chunk) / 2 / rate if chunk else 1.0   # An empty chunk = 1 s with no audio.
            heard += duration
            if chunk and full.AcceptWaveform(chunk):
                texts.append(self._text(full.Result()))
                break
            partial = json.loads(full.PartialResult()).get("partial", "")
            quiet = 0.0 if partial != last_partial else quiet + duration
            last_partial = partial
            if quiet >= settings.VOICE_END_SILENCE or heard >= settings.VOICE_LISTEN_SECONDS:
                break

        texts.append(self._text(full.FinalResult()))
        return apply_corrections(" ".join(t for t in texts if t))
