"""
Voice activity detection: is someone speaking right now?

Energy-based and adaptive. Each 30 ms frame's loudness (RMS) is compared
with the room's background level, which the detector keeps learning
from frames that aren't speech, so a quiet bedroom and a noisy hostel
corridor both work. Speech starts after a few loud frames in a row (so
a click or a cough doesn't count) and ends after a stretch of quiet.

    detector = VoiceActivityDetector(rate=16000)
    for chunk in audio:
        detector.feed(chunk)
        if detector.ended or detector.gave_up:
            break

It uses only the standard library and NumPy-free arithmetic on 16-bit
PCM, so it adds no dependency and costs almost nothing per chunk.
"""

import math
import struct
from dataclasses import dataclass

from aurora.config import settings

FRAME_SECONDS = 0.03


@dataclass
class VadSettings:
    ratio: float = 3.0              # speech is this many times louder than the background
    minimum_rms: float = 300.0      # never call anything quieter than this speech (16-bit scale)
    start_frames: int = 3           # ~90 ms of loud frames to start
    end_silence: float = 0.9        # seconds of quiet that end speech
    no_speech_timeout: float = 5.0  # give up if nobody starts speaking
    adapt: float = 0.05             # how quickly the background level follows the room
    calibration: float = 0.2        # the first moments only teach the room's level (nobody
                                    # starts talking within 0.2 s of pressing the button)


def rms(frame: bytes) -> float:
    count = len(frame) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", frame[: count * 2])
    return math.sqrt(sum(s * s for s in samples) / count)


class VoiceActivityDetector:

    def __init__(self, rate: int = 16000, config: VadSettings | None = None):
        self.rate = rate
        self.config = config or VadSettings(
            ratio=settings.VOICE_VAD_RATIO, minimum_rms=settings.VOICE_VAD_MIN_RMS,
            end_silence=settings.VOICE_END_SILENCE, no_speech_timeout=settings.VOICE_NO_SPEECH_SECONDS)
        self.frame_bytes = max(2, int(rate * FRAME_SECONDS) * 2)
        self.background: float | None = None
        self.started = False          # speech has begun
        self.ended = False            # speech began and then stopped
        self.elapsed = 0.0            # seconds of audio seen
        self.speech_seconds = 0.0
        self._loud_run = 0
        self._quiet = 0.0
        self._buffer = b""
        self._calibration: list[float] = []
        self.level = 0.0              # loudness of the last frame (for a level meter)

    @property
    def threshold(self) -> float:
        background = self.background if self.background is not None else self.config.minimum_rms / self.config.ratio
        return max(self.config.minimum_rms, background * self.config.ratio)

    @property
    def gave_up(self) -> bool:
        """Nobody started speaking within the timeout."""
        return not self.started and self.elapsed >= self.config.no_speech_timeout

    def feed(self, chunk: bytes) -> None:
        """Process raw 16-bit mono PCM. An empty chunk counts as one second of
        nothing arriving (as the microphone source signals a timeout)."""
        if not chunk:
            self._frame_result(loud=False, seconds=1.0)
            return
        self._buffer += chunk
        while len(self._buffer) >= self.frame_bytes:
            frame, self._buffer = self._buffer[: self.frame_bytes], self._buffer[self.frame_bytes:]
            self.level = rms(frame)
            if self.elapsed < self.config.calibration:
                # Calibration: learn the room, whatever its level. Without this, a room
                # louder than minimum_rms would look like speech from the first frame.
                self._calibration.append(self.level)
                self.background = sum(self._calibration) / len(self._calibration)
                self.elapsed += len(frame) / 2 / self.rate
                continue
            loud = self.level >= self.threshold
            if not loud and not self.started:
                # Learn the room only from quiet frames before speech.
                if self.background is None:
                    self.background = self.level
                else:
                    self.background += self.config.adapt * (self.level - self.background)
            self._frame_result(loud, len(frame) / 2 / self.rate)

    def _frame_result(self, loud: bool, seconds: float) -> None:
        self.elapsed += seconds
        if self.ended:
            return
        if loud:
            self._loud_run += 1
            self._quiet = 0.0
            if self.started or self._loud_run >= self.config.start_frames:
                if not self.started:
                    self.started = True
                    self.speech_seconds += seconds * (self._loud_run - 1)
                self.speech_seconds += seconds
        else:
            self._loud_run = 0
            if self.started:
                self._quiet += seconds
                if self._quiet >= self.config.end_silence:
                    self.ended = True
