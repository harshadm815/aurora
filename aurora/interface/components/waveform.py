"""
The voice waveform under the AI core.

    listening   bars follow the microphone's real loudness (the same level
                the voice control reports), newest on the right
    speaking    a gentle moving pattern while a reply is spoken. It shows
                *that* AURORA is speaking; the speech's loudness isn't
                measured (Windows' speech engine doesn't report it)
    otherwise   a faint flat line, and nothing is redrawn

It is redrawn only while listening or speaking, at most 20 times a second
(5 in performance mode, and still with animations off).
"""

import math
from collections import deque

from PySide6.QtCore import QElapsedTimer, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

BARS = 32
ACTIVE = ("listening", "speaking")
FRAME_MS = 50              # 20 frames a second
REDUCED_FRAME_MS = 200     # 5, in performance mode


def speaking_heights(t: float, bars: int = BARS) -> list[float]:
    """0..1 bar heights for the speaking pattern at time t (seconds)."""
    heights = []
    for i in range(bars):
        x = i / max(1, bars - 1)
        envelope = math.sin(math.pi * x)                    # taller in the middle
        wave = 0.55 + 0.45 * math.sin(t * 6.0 + i * 0.7) * math.sin(t * 2.3 + i * 0.31)
        heights.append(max(0.06, envelope * wave))
    return heights


class Waveform(QWidget):

    def __init__(self, accent: str = "#22d3ee", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(34)
        self.accent = QColor(accent)
        self.mode = "idle"
        self.reduced = False
        self.animated = True
        self.levels: deque[float] = deque([0.0] * BARS, maxlen=BARS)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_paint = -10_000
        self._timer = QTimer(self)                 # only for the speaking pattern
        self._timer.timeout.connect(self.update)
        self.setToolTip("Voice: moves while AURORA is listening or speaking")

    def sizeHint(self):
        return QSize(260, 34)

    @property
    def active(self) -> bool:
        return self.mode in ACTIVE

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _frame_ms(self) -> int:
        return REDUCED_FRAME_MS if self.reduced else FRAME_MS

    def set_state(self, state: str) -> None:
        mode = state if state in ACTIVE else "idle"
        if mode == self.mode:
            return
        self.mode = mode
        self.levels = deque([0.0] * BARS, maxlen=BARS)
        self.setToolTip({"listening": "Listening: the bars follow your microphone's loudness",
                         "speaking": "Speaking a reply (the pattern shows that AURORA is speaking, not how loud)",
                         }.get(mode, "Voice: moves while AURORA is listening or speaking"))
        self._schedule()
        self.update()

    def set_reduced(self, on: bool) -> None:
        self.reduced = on
        self._schedule()

    def set_animated(self, on: bool) -> None:
        self.animated = on
        self._schedule()
        self.update()

    def set_accent(self, accent: str) -> None:
        self.accent = QColor(accent)
        self.update()

    def push(self, level: float) -> None:
        """A microphone level, 0..1. Redraws at most once per frame interval."""
        if self.mode != "listening":
            return
        self.levels.append(max(0.0, min(1.0, level)))
        now = self._clock.elapsed()
        if self.animated and now - self._last_paint >= self._frame_ms():
            self._last_paint = now
            self.update()

    def _schedule(self) -> None:
        if self.mode == "speaking" and self.animated and self.isVisible():
            self._timer.start(self._frame_ms())
        else:
            self._timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def heights(self) -> list[float]:
        if self.mode == "listening":
            return list(self.levels)
        if self.mode == "speaking":
            return speaking_heights(self._clock.elapsed() / 1000.0 if self.animated else 0.6)
        return [0.0] * BARS

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        if not self.active:
            colour = QColor(self.accent)
            colour.setAlpha(50)
            painter.fillRect(QRectF(width * 0.15, height / 2 - 0.5, width * 0.7, 1), colour)
            painter.end()
            return
        bars = self.heights()
        slot = width * 0.8 / len(bars)
        left = width * 0.1
        painter.setPen(Qt.NoPen)
        for i, value in enumerate(bars):
            bar = max(2.0, value * (height - 4))
            colour = QColor(self.accent)
            colour.setAlpha(int(110 + 145 * value))
            painter.setBrush(colour)
            painter.drawRoundedRect(QRectF(left + i * slot + slot * 0.2, (height - bar) / 2, slot * 0.6, bar), 1.5, 1.5)
        painter.end()
