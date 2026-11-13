"""
Piper: a natural-sounding neural voice that runs on this PC.

Measured on the development laptop (i3-8130U): about 5 times faster than
real time, the first sentence ready in about 0.4 s, 120 MB of RAM once
loaded and about 255 MB while speaking (Docs/VOICE_WAKE_MOBILE.md).

    voice = PiperSpeaker()                 # loads Models/tts/piper/<PIPER_VOICE>.onnx
    voice.speak("Hello. How can I help?")  # False if interrupted

Sentences are made one after another on a background thread while the
previous one plays, so after the first sentence there's no gap. Playing is
done in 0.1-second blocks and stops within one block when ``stop()`` is
called or ``should_stop()`` becomes true.

Piper and its pronunciation library (espeak-ng) are GPL-3.0. AURORA uses
them on this PC; that places no conditions on use, only on giving the
packaged program to others (see the blueprint). Voice models are
downloaded separately into Models/tts/piper/ and each has its own licence
in its MODEL_CARD (the default, LJSpeech, is public-domain data).
"""

import logging
import queue
import threading
import wave
from pathlib import Path

from aurora.config import settings
from aurora.voice.speaker import Speaker, speech_text

logger = logging.getLogger(__name__)

NATURAL_WPM = 170          # Piper voices speak at roughly this speed with length_scale 1
BLOCK_SECONDS = 0.1


def length_scale(words_per_minute: int) -> float:
    """Piper's length scale for a speaking speed (smaller is faster), kept between 0.5 and 2."""
    return max(0.5, min(2.0, NATURAL_WPM / max(1, words_per_minute)))


def _sounddevice_output(rate: int):
    import sounddevice
    stream = sounddevice.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
    stream.start()
    return stream


class PiperSpeaker(Speaker):
    """A Piper voice (a provider: see providers.py)."""

    name = "piper"
    local = True

    def __init__(self, voice: str | None = None, rate: int | None = None, volume: float | None = None,
                 pitch: int | None = None, folder: str | Path | None = None, output=None):
        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as error:
            raise RuntimeError("The 'piper-tts' package isn't installed.") from error
        self._config_type = SynthesisConfig
        self.folder = Path(folder or settings.PIPER_VOICE_DIR)
        wanted = voice if voice is not None else settings.PIPER_VOICE
        path = self._find(wanted) or self._find(settings.PIPER_VOICE)
        if path is None:
            raise RuntimeError(f"No Piper voice in {self.folder}. Download one (an .onnx file and its .onnx.json) "
                               "from huggingface.co/rhasspy/piper-voices into that folder.")
        self.voice_name = path.stem
        self.voice = PiperVoice.load(str(path))
        self.rate = self.voice.config.sample_rate
        self._open_output = output or _sounddevice_output
        self._stop = threading.Event()
        self.set_rate(rate or settings.VOICE_RATE)
        self.set_volume(settings.VOICE_VOLUME if volume is None else volume)
        self.pitch = 0                       # Piper has no pitch control; the setting applies to Windows voices

    # ------------------------------------------------------------- settings

    def _find(self, wanted: str | None) -> Path | None:
        wanted = (wanted or "").lower()
        models = sorted(self.folder.glob("*.onnx")) if self.folder.is_dir() else []
        models = [m for m in models if m.with_suffix(".onnx.json").is_file()]
        for model in models:
            if wanted and wanted in model.stem.lower():
                return model
        return models[0] if models and not wanted else None

    def voices(self) -> list[str]:
        if not self.folder.is_dir():
            return []
        return [m.stem for m in sorted(self.folder.glob("*.onnx")) if m.with_suffix(".onnx.json").is_file()]

    def set_voice(self, wanted: str | None) -> bool:
        """Only the loaded voice can be used without reloading; True if it matches."""
        return bool(wanted) and wanted.lower() in self.voice_name.lower()

    def set_rate(self, words_per_minute: int) -> None:
        self.length = length_scale(words_per_minute)

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))

    def set_pitch(self, pitch: int) -> None:
        """Piper voices have no pitch control (kept so all voices accept the same settings)."""

    def _config(self):
        return self._config_type(length_scale=self.length, volume=self.volume)

    # --------------------------------------------------------------- speech

    def _audio(self, text: str):
        """int16 audio, one piece per sentence (Piper splits the text into sentences)."""
        for chunk in self.voice.synthesize(text, syn_config=self._config()):
            yield chunk.audio_int16_bytes

    def speak(self, text: str, should_stop=lambda: False) -> bool:
        spoken = speech_text(text)
        if not spoken:
            return True
        self._stop.clear()
        finished = threading.Event()               # this reply only: a later reply can't revive its maker
        stopped = lambda: finished.is_set() or self._stop.is_set() or should_stop()       # noqa: E731
        pieces: queue.Queue = queue.Queue(maxsize=2)

        def hand_over(item) -> bool:
            while True:
                try:
                    pieces.put(item, timeout=0.1)
                    return True
                except queue.Full:
                    if stopped():
                        return False

        def make():
            try:
                for audio in self._audio(spoken):
                    if stopped() or not hand_over(audio):
                        return
            except Exception as error:                 # reported to the speaking thread
                hand_over(error)
                return
            hand_over(None)

        maker = threading.Thread(target=make, name="aurora-piper", daemon=True)
        maker.start()
        output = None
        block = int(self.rate * BLOCK_SECONDS) * 2
        try:
            while True:
                try:
                    audio = pieces.get(timeout=0.05)
                except queue.Empty:
                    if stopped():
                        return self._interrupted()
                    continue
                if audio is None:
                    return True
                if isinstance(audio, Exception):
                    raise audio
                if output is None:
                    output = self._open_output(self.rate)
                for start in range(0, len(audio), block):
                    if stopped():
                        return self._interrupted()
                    output.write(audio[start:start + block])
        finally:
            # The maker thread sees the flag and ends after its current sentence; nothing waits for it.
            finished.set()
            if output is not None:
                output.stop()
                output.close()

    @staticmethod
    def _interrupted() -> bool:
        logger.info("Speech interrupted")
        return False

    def stop(self) -> None:
        self._stop.set()

    def save_to_file(self, text: str, path) -> None:
        """Speech written to a WAV file instead of the speakers (for tests)."""
        with wave.open(str(path), "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(self.rate)
            for audio in self._audio(speech_text(text)):
                file.writeframes(audio)
