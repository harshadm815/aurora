"""
AURORA's core: the orb in the middle of the Dashboard. It shows what
AURORA is doing (Backend.ai_state), not decoration:

    idle        the core breathes slowly (the only motion while nothing happens)
    thinking    the three rings turn at different speeds, with nodes on them
    responding  particles flow out from the core while the reply is written
    speaking    the same as responding, while a reply is spoken
    listening   rings expand outward with the microphone's loudness
    processing  a progress arc sweeps (starting up)
    error       red core and a warning mark; no motion
    offline     grey and still (paused or stopped)

Everything is drawn with QPainter (no images, blur or OpenGL), at 15
frames a second while busy and 10 while idle, and only while the orb is
visible. The glow and still rings are drawn once and reused (measured on
the i3-8130U: anti-aliased arcs were most of a frame's cost). With
animations off (Settings), each state is a still picture. In performance
mode (aurora/interface/performance.py) the core is still while idle and
busy states run at 5 frames a second.
"""

import math
from functools import lru_cache

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

STATES = ("idle", "thinking", "responding", "speaking", "listening", "processing", "error", "offline")
LABELS = {
    "idle": "Ready", "thinking": "Thinking", "responding": "Responding", "speaking": "Speaking",
    "listening": "Listening", "processing": "Starting", "error": "Something went wrong", "offline": "Paused",
}
ERROR_COLOUR = "#f87171"
OFFLINE_COLOUR = "#475569"
STILL = {"error", "offline"}
FRAME_MS = {"idle": 100}           # 10 frames a second: breathing is slow
BUSY_FRAME_MS = 66                 # 15: busy states are when the model needs the CPU most
REDUCED_FRAME_MS = 200             # 5, in performance mode


def frame_interval(state: str, reduced: bool = False) -> int | None:
    """Milliseconds between frames, or None for a still picture."""
    if state in STILL or (reduced and state == "idle"):
        return None
    if reduced:
        return REDUCED_FRAME_MS
    return FRAME_MS.get(state, BUSY_FRAME_MS)


class CoreOrb(QWidget):

    def __init__(self, accent: str = "#22d3ee", parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(180, 180)
        self.accent = QColor(accent)
        self.state = "processing"
        self.level = 0.0                     # microphone loudness 0..1 while listening
        self.animated = True
        self.reduced = False                 # performance mode
        self._clock = QElapsedTimer()
        self._clock.start()
        self._frozen = 0.0                   # the time drawn when animations are off
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._frame)

    def sizeHint(self):
        return QSize(300, 300)

    # ---------------------------------------------------------------- control

    def set_state(self, state: str) -> None:
        self.state = state if state in STATES else "idle"
        if self.state != "listening":
            self.level = 0.0
        self.setToolTip(LABELS[self.state])
        self._schedule()
        self.update()

    def set_level(self, level: float) -> None:
        self.level = max(0.0, min(1.0, level))

    def set_accent(self, accent: str) -> None:
        self.accent = QColor(accent)
        self.update()

    def set_animated(self, on: bool) -> None:
        self.animated = on
        self._schedule()
        self.update()

    def set_reduced(self, on: bool) -> None:
        if on != self.reduced:
            self.reduced = on
            self._schedule()
            self.update()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def _schedule(self) -> None:
        interval = frame_interval(self.state, self.reduced)
        if self.animated and interval is not None and self.isVisible():
            self._timer.start(interval)
        else:
            self._timer.stop()

    def _frame(self) -> None:
        if self.state == "idle":
            # Only the core breathes while idle, so only it is redrawn.
            size = min(self.width(), self.height())
            half = size * 0.15 * 1.3
            self.update(int(self.width() / 2 - half), int(self.height() / 2 - half), int(2 * half) + 2,
                        int(2 * half) + 2)
        else:
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()                   # no painting while the orb can't be seen

    # ---------------------------------------------------------------- drawing

    def _time(self) -> float:
        if not self.animated or (self.reduced and self.state == "idle"):
            return self._frozen
        return self._clock.elapsed() / 1000.0

    def colour(self) -> QColor:
        if self.state == "error":
            return QColor(ERROR_COLOUR)
        if self.state == "offline":
            return QColor(OFFLINE_COLOUR)
        return QColor(self.accent)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        size = min(self.width(), self.height())
        centre = QPointF(self.width() / 2, self.height() / 2)
        paint(painter, centre, size, self.state, self._time(), self.colour(), self.level)
        painter.end()


def _with_alpha(colour: QColor, alpha: float) -> QColor:
    result = QColor(colour)
    result.setAlpha(max(0, min(255, int(alpha))))
    return result


def _ring(painter, centre, radius, colour, width, start=0.0, span=360.0):
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    rect = QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius)
    if span >= 360:
        painter.drawEllipse(rect)
    else:
        painter.drawArc(rect, int(start * 16), int(span * 16))


@lru_cache(maxsize=16)
def _halo(radius: int, rgba: int) -> QPixmap:
    colour = QColor.fromRgba(rgba)
    pixmap = QPixmap(2 * radius + 2, 2 * radius + 2)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    centre = QPointF(radius + 1, radius + 1)
    gradient = QRadialGradient(centre, radius)
    gradient.setColorAt(0.0, _with_alpha(colour, 96))
    gradient.setColorAt(0.45, _with_alpha(colour, 30))
    gradient.setColorAt(1.0, _with_alpha(colour, 0))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(centre, radius, radius)
    painter.end()
    return pixmap


def _rings(size: float) -> list[tuple[float, int, int, int]]:
    return [(size * 0.27, 30, 290, 210), (size * 0.355, 200, 250, 150), (size * 0.44, 110, 300, 95)]


@lru_cache(maxsize=16)
def _still_rings(size: int, rgba: int, dim: bool) -> QPixmap:
    colour = QColor.fromRgba(rgba)
    pixmap = QPixmap(size + 4, size + 4)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    centre = QPointF(pixmap.width() / 2, pixmap.height() / 2)
    for radius, start, span, alpha in _rings(size):
        _ring(painter, centre, radius, _with_alpha(colour, alpha * (0.55 if dim else 1)), max(1.5, size / 110),
              start, span)
    painter.end()
    return pixmap


def paint(painter: QPainter, centre: QPointF, size: float, state: str, t: float, colour: QColor,
          level: float = 0.0) -> None:
    """Draws one frame. Separate from the widget so tests and exports can draw it too."""
    outer = size * 0.46
    core = size * 0.15
    rings = _rings(size)

    breath = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 4.0)       # one breath every 4 seconds
    if state == "idle":
        glow = 40                      # steady: the core breathes (redrawing the whole halo cost CPU)
    elif state in STILL:
        glow = 14 if state == "error" else 0
    else:
        glow = 60

    # Halo: drawn once per size and colour, then only faded (a full-size
    # gradient every frame cost about a fifth of a CPU core).
    if glow:
        halo = _halo(int(outer), colour.rgba())
        painter.save()
        painter.setOpacity(min(1.0, glow / 60))
        painter.drawPixmap(QPointF(centre.x() - halo.width() / 2, centre.y() - halo.height() / 2), halo)
        painter.restore()

    # Rings
    speeds = {"thinking": (70, -45, 28), "responding": (14, -9, 6), "speaking": (14, -9, 6),
              "processing": (8, -5, 3)}.get(state, (0, 0, 0))
    width = max(1.5, size / 110)
    if any(speeds):
        for (radius, start, span, alpha), speed in zip(rings, speeds):
            _ring(painter, centre, radius, _with_alpha(colour, alpha), width, start + speed * t, span)
    else:
        # Still rings are drawn once and reused: anti-aliased arcs are most of a frame's cost.
        layer = _still_rings(int(size), colour.rgba(), state == "offline")
        painter.drawPixmap(QPointF(centre.x() - layer.width() / 2, centre.y() - layer.height() / 2), layer)

    if state == "thinking":
        # Nodes on the turning rings, linked to the core: a small neural network.
        nodes = []
        for index, ((radius, start, _, _), speed) in enumerate(zip(rings, speeds)):
            for k in range(3):
                angle = math.radians(start + speed * t + k * 120 + index * 40)
                nodes.append(QPointF(centre.x() + radius * math.cos(angle), centre.y() - radius * math.sin(angle)))
        painter.setPen(QPen(_with_alpha(colour, 45), 1))
        for a, b in zip(nodes, nodes[3:]):
            painter.drawLine(a, b)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_with_alpha(colour, 230))
        for node in nodes:
            painter.drawEllipse(node, width * 1.6, width * 1.6)

    elif state in ("responding", "speaking"):
        # Particles flowing outward along gentle spirals.
        count = 16
        painter.setPen(Qt.NoPen)
        for i in range(count):
            p = (t * 0.32 + i / count) % 1.0
            radius = core * 1.2 + p * (outer - core * 1.2)
            angle = i * 2 * math.pi / count + p * 1.3
            painter.setBrush(_with_alpha(colour, (1 - p) * 220))
            point = QPointF(centre.x() + radius * math.cos(angle), centre.y() + radius * math.sin(angle))
            painter.drawEllipse(point, width * (1.8 - p), width * (1.8 - p))

    elif state == "listening":
        # Sound waves: rings expanding from the core, bigger when it's louder.
        for k in range(3):
            p = (t * 0.7 + k / 3) % 1.0
            radius = core + p * (outer - core) * (0.55 + 0.45 * max(level, 0.15))
            _ring(painter, centre, radius, _with_alpha(colour, (1 - p) * 200), width * 1.4)

    elif state == "processing":
        sweep = (t * 220) % 360
        _ring(painter, centre, size * 0.40, _with_alpha(colour, 40), width * 2.2)
        _ring(painter, centre, size * 0.40, _with_alpha(colour, 230), width * 2.2, -sweep, 80)

    # Core
    gradient = QRadialGradient(QPointF(centre.x() - core * 0.3, centre.y() - core * 0.3), core * 1.4)
    light = QColor(colour).lighter(170) if state != "offline" else QColor(colour).lighter(120)
    gradient.setColorAt(0.0, _with_alpha(light, 255))
    gradient.setColorAt(0.55, _with_alpha(colour, 235))
    gradient.setColorAt(1.0, _with_alpha(QColor(colour).darker(220), 255))
    scale = 1.0 + (0.04 * breath if state == "idle" else 0.0) + (0.12 * level if state == "listening" else 0.0)
    painter.setPen(QPen(_with_alpha(colour, 200), width))
    painter.setBrush(gradient)
    painter.drawEllipse(centre, core * scale, core * scale)

    if state == "error":
        font = QFont("Segoe UI", max(8, int(core * 0.9)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#1c1014"))
        painter.drawText(QRectF(centre.x() - core, centre.y() - core, 2 * core, 2 * core), Qt.AlignCenter, "!")
