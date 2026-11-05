"""
Text-to-speech.

``Pyttsx3Speaker`` uses the voices built into Windows (SAPI5), fully
offline. ``speech_text()`` turns a screen reply into something pleasant
to hear: no links, units spelled out, and long replies cut short with
"the rest is on screen".
"""

import logging
import re

from aurora.config import settings

logger = logging.getLogger(__name__)

_URL = re.compile(r"\(?https?://\S+\)?")
_SOURCE_LINE = re.compile(r"^\(?Source:.*$", re.IGNORECASE | re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+\.)\s+", re.MULTILINE)
_REPLACEMENTS = [
    (re.compile(r"(\d)\s*°C"), r"\1 degrees Celsius"),
    (re.compile(r"(\d)\s*–\s*(\d)"), r"\1 to \2"),
    (re.compile(r"\bkm/h\b"), "kilometres per hour"),
    (re.compile(r"(\d)\s*%"), r"\1 percent"),
    (re.compile(r"\s*->\s*|\s*→\s*"), " to "),
    (re.compile(r"[*_`#>]"), ""),
]


def speech_text(text: str, max_chars: int | None = None) -> str:
    """A reply rewritten for listening rather than reading."""
    max_chars = settings.VOICE_MAX_SPOKEN_CHARS if max_chars is None else max_chars

    text = _SOURCE_LINE.sub("", text)
    text = _URL.sub("", text)
    text = _BULLET.sub("", text)
    for pattern, replacement in _REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = " ".join(text.split())

    if len(text) > max_chars:
        cut = text[:max_chars]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = (cut[: end + 1] if end > max_chars // 3 else cut.rsplit(" ", 1)[0]) + " The rest is on screen."
    return text


class Speaker:
    """Interface: anything that can say text out loud."""

    def speak(self, text: str) -> None:
        raise NotImplementedError


class Pyttsx3Speaker(Speaker):

    def __init__(self, rate: int | None = None, voice: str | None = None):
        import pyttsx3  # Imported lazily: AURORA runs without voice installed.

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", rate or settings.VOICE_RATE)

        wanted = (voice or settings.VOICE_NAME or "").lower()
        if wanted:
            for available in self.engine.getProperty("voices"):
                if wanted in available.name.lower():
                    self.engine.setProperty("voice", available.id)
                    break

    def speak(self, text: str) -> None:
        spoken = speech_text(text)
        if not spoken:
            return
        self.engine.say(spoken)
        self.engine.runAndWait()   # Blocks until finished, so AURORA never hears itself.
