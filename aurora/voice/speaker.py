"""
Text-to-speech.

``SapiSpeaker`` uses the voices built into Windows (SAPI5) directly,
through comtypes, fully offline. ``speech_text()`` turns a screen reply
into something pleasant to hear: no links or citation lists, units
spelled out, and long replies cut short with "the rest is on screen".

Why not pyttsx3 (used until 0.28): with pyttsx3 2.99 on Windows, only
the first runAndWait() of an engine actually speaks; later calls return
at once in silence (or hang, when writing to a file), and its word
callbacks never fire, so speech could neither continue past one
utterance nor be interrupted. SAPI itself has none of these problems.

Speech is interruptible: SAPI speaks in the background while the
speaking thread checks every 50 ms whether it should stop (a Stop
button, a key press, the user starting to talk). Stopping purges the
utterance, which takes effect within a fraction of a second.

COM objects belong to the thread that created them, so a speaker must
be created and used on one thread; ``stop()`` only raises a flag and is
safe from any thread.
"""

import logging
import re
import threading

from aurora.config import settings

logger = logging.getLogger(__name__)

_URL = re.compile(r"\(?https?://\S+\)?")
_SOURCE_LINE = re.compile(r"^\(?Sources?:.*$", re.IGNORECASE | re.MULTILINE)
_CITATION = re.compile(r"\s*\[\d+\]")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+", re.MULTILINE)
_REPLACEMENTS = [
    (re.compile(r"(\d)\s*°C"), r"\1 degrees Celsius"),
    (re.compile(r"(\d)\s*°F"), r"\1 degrees Fahrenheit"),
    (re.compile(r"(\d)\s*–\s*(\d)"), r"\1 to \2"),
    (re.compile(r"\bkm/h\b"), "kilometres per hour"),
    (re.compile(r"(\d)\s*%"), r"\1 percent"),
    (re.compile(r"\s*->\s*|\s*→\s*"), " to "),
    (re.compile(r"[*_`#>“”\"]"), ""),
]
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

# SAPI flags
SVSF_ASYNC = 1
SVSF_PURGE = 2
SSFM_CREATE_FOR_WRITE = 3
POLL_MS = 50


def speech_text(text: str, max_chars: int | None = None) -> str:
    """A reply rewritten for listening rather than reading."""
    max_chars = settings.VOICE_MAX_SPOKEN_CHARS if max_chars is None else max_chars

    # Citation lists ("Sources: [1] thesis.pdf, page 2") are for reading, not listening.
    text = re.split(r"\n\s*Sources?:\s*\n", text, maxsplit=1)[0]
    text = _SOURCE_LINE.sub("", text)
    text = _URL.sub("", text)
    text = _CITATION.sub("", text)
    text = _BULLET.sub("", text)
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = " ".join(text.split())

    if len(text) > max_chars:
        cut = text[:max_chars]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = (cut[: end + 1] if end > max_chars // 3 else cut.rsplit(" ", 1)[0]) + " The rest is on screen."
    return text


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE.split(text) if s.strip()]


def sapi_rate(words_per_minute: int) -> int:
    """SAPI's -10..10 scale; 0 is about 180 words per minute, each step about 10 % faster or slower."""
    return max(-10, min(10, round((words_per_minute - 180) / 18)))


class Speaker:
    """Interface: anything that can say text out loud."""

    def speak(self, text: str, should_stop=lambda: False) -> bool:
        """Says ``text``; returns False if it was interrupted."""
        raise NotImplementedError

    def stop(self) -> None:
        """Ask a running speak() to stop as soon as possible (any thread)."""


class SapiSpeaker(Speaker):

    def __init__(self, rate: int | None = None, voice: str | None = None, volume: float | None = None):
        try:
            import comtypes
            import comtypes.client
        except ImportError as error:
            raise RuntimeError("The 'comtypes' package isn't installed.") from error
        comtypes.CoInitialize()        # this thread may not have COM yet (the GUI's voice thread)
        self._client = comtypes.client
        self.voice = comtypes.client.CreateObject("SAPI.SpVoice")
        self.set_rate(rate or settings.VOICE_RATE)
        self.set_volume(settings.VOICE_VOLUME if volume is None else volume)
        self.set_voice(voice if voice is not None else settings.VOICE_NAME)
        self._stop = threading.Event()

    # ------------------------------------------------------------- settings

    def voices(self) -> list[str]:
        tokens = self.voice.GetVoices()
        return [tokens.Item(i).GetDescription() for i in range(tokens.Count)]

    def set_voice(self, wanted: str | None) -> bool:
        """Choose a voice by part of its name ("Zira", "David"). False if there's none."""
        wanted = (wanted or "").lower()
        if not wanted:
            return False
        tokens = self.voice.GetVoices()
        for i in range(tokens.Count):
            if wanted in tokens.Item(i).GetDescription().lower():
                self.voice.Voice = tokens.Item(i)
                return True
        return False

    def set_rate(self, words_per_minute: int) -> None:
        self.voice.Rate = sapi_rate(words_per_minute)

    def set_volume(self, volume: float) -> None:
        self.voice.Volume = round(max(0.0, min(1.0, volume)) * 100)

    # --------------------------------------------------------------- speech

    def speak(self, text: str, should_stop=lambda: False) -> bool:
        spoken = speech_text(text)
        if not spoken:
            return True
        self._stop.clear()
        for sentence in sentences(spoken):
            self.voice.Speak(sentence, SVSF_ASYNC)
            while not self.voice.WaitUntilDone(POLL_MS):
                if self._stop.is_set() or should_stop():
                    self.voice.Speak("", SVSF_ASYNC | SVSF_PURGE)    # stops the current utterance at once
                    self.voice.WaitUntilDone(1000)
                    logger.info("Speech interrupted")
                    return False
            if self._stop.is_set() or should_stop():
                logger.info("Speech interrupted")
                return False
        return True

    def stop(self) -> None:
        self._stop.set()

    def save_to_file(self, text: str, path) -> None:
        """Speech written to a WAV file instead of the speakers (for tests)."""
        stream = self._client.CreateObject("SAPI.SpFileStream")
        stream.Open(str(path), SSFM_CREATE_FOR_WRITE)
        previous = self.voice.AudioOutputStream
        self.voice.AudioOutputStream = stream
        try:
            self.voice.Speak(speech_text(text), 0)
        finally:
            stream.Close()
            self.voice.AudioOutputStream = previous
