"""
Speech for the phone, done on the PC (no cloud speech service).

  - ``hear(wav)``: the phone's recording (16-bit mono WAV, sent while you
    hold the talk button) → text, with the same Vosk model and clean-up
    as the PC's microphone. Nothing is saved; the audio is dropped after.
  - ``say(text)``: text → WAV in AURORA's Piper voice, for the phone to
    play (replies and reminders).

The recogniser and the voice are loaded the first time the phone needs
them and freed after SPEECH_KEEP_MINUTES without use (about 60-100 MB
and 120 MB), one request at a time.
"""

import io
import logging
import threading
import time
import wave

from aurora.config import settings

logger = logging.getLogger(__name__)

SPEECH_KEEP_MINUTES = 10
MAX_SECONDS = 60
MAX_SAY_CHARS = 2000


class SpeechError(Exception):
    pass


def read_wav(data: bytes) -> tuple[bytes, int]:
    """PCM and sample rate from a 16-bit mono WAV. Raises SpeechError otherwise."""
    try:
        with wave.open(io.BytesIO(data), "rb") as file:
            if file.getnchannels() != 1 or file.getsampwidth() != 2:
                raise SpeechError("The recording must be 16-bit mono.")
            rate = file.getframerate()
            if not 8000 <= rate <= 48000:
                raise SpeechError("Unsupported sample rate.")
            if file.getnframes() > rate * MAX_SECONDS:
                raise SpeechError(f"Recordings can be up to {MAX_SECONDS} seconds.")
            return file.readframes(file.getnframes()), rate
    except (wave.Error, EOFError) as error:
        raise SpeechError("That isn't a WAV recording.") from error


def make_wav(pcm: bytes, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(pcm)
    return buffer.getvalue()


def _listener():
    from aurora.voice.listener import Listener
    return Listener()


def _voice():
    from aurora.voice.piper_voice import PiperSpeaker
    return PiperSpeaker()


class PhoneSpeech:

    def __init__(self, listener_factory=_listener, voice_factory=_voice, clock=time.monotonic,
                 keep_seconds: float = SPEECH_KEEP_MINUTES * 60):
        self.listener_factory = listener_factory
        self.voice_factory = voice_factory
        self.clock = clock
        self.keep_seconds = keep_seconds
        self._listener = None
        self._voice = None
        self._used = 0.0
        self._lock = threading.Lock()

    def hear(self, wav: bytes) -> str:
        from aurora.voice.listener import numbers_to_digits

        pcm, rate = read_wav(wav)
        with self._lock:
            if self._listener is None:
                try:
                    self._listener = self.listener_factory()
                except Exception as error:
                    raise SpeechError(f"Speech recognition isn't available on the PC: {error}") from error
            self._used = self.clock()
            text = self._listener.transcribe(pcm, rate)
        return numbers_to_digits(text) if text else ""

    def say(self, text: str) -> bytes:
        from aurora.voice.speaker import speech_text

        spoken = speech_text(text)[:MAX_SAY_CHARS]
        if not spoken:
            raise SpeechError("Nothing to say.")
        with self._lock:
            if self._voice is None:
                try:
                    self._voice = self.voice_factory()
                except Exception as error:
                    raise SpeechError(f"AURORA's voice isn't available on the PC: {error}") from error
            self._used = self.clock()
            self._voice.set_rate(settings.VOICE_RATE)
            pcm = b"".join(self._voice._audio(spoken))
            return make_wav(pcm, self._voice.rate)

    def release_if_idle(self) -> bool:
        """Frees the recogniser and voice after a while without use. True if something was freed."""
        with self._lock:
            if (self._listener or self._voice) and self.clock() - self._used > self.keep_seconds:
                self._listener = self._voice = None
                logger.info("Mobile: phone speech released after %d minutes unused", self.keep_seconds // 60)
                return True
        return False
