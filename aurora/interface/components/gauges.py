"""Round indicators: a value as an arc, with the number in the middle."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from aurora.interface.components.widgets import STATE_COLOURS


def level_colour(percent: float | None, accent: str, warn: float = 75, high: float = 90,
                 more_is_better: bool = False) -> str:
    """The accent normally; amber above warn; red above high. With more_is_better
    (tools ready), the other way round: amber below 100 %, red below half."""
    if percent is None:
        return STATE_COLOURS["off"]
    if more_is_better:
        return accent if percent >= 100 else STATE_COLOURS["warning"] if percent >= 50 else STATE_COLOURS["error"]
    if percent >= high:
        return STATE_COLOURS["error"]
    if percent >= warn:
        return STATE_COLOURS["warning"]
    return accent


class RingGauge(QWidget):
    """A circular gauge: 0-100 % as an arc, the value in the middle, a caption below it."""

    def __init__(self, caption: str, accent: str = "#22d3ee", more_is_better: bool = False, parent=None):
        super().__init__(parent)
        self.caption = caption
        self.more_is_better = more_is_better
        self.accent = accent
        self.percent: float | None = None
        self.text = "—"
        self.detail = ""
        self.setMinimumSize(96, 112)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(112, 120)

    def set(self, percent: float | None, text: str | None = None, detail: str = "") -> None:
        self.percent = None if percent is None else max(0.0, min(100.0, float(percent)))
        self.text = text if text is not None else ("—" if percent is None else f"{percent:.0f}%")
        self.detail = detail
        self.setToolTip(f"{self.caption}: {self.text}" + (f"\n{detail}" if detail else ""))
        self.update()

    def set_accent(self, accent: str) -> None:
        self.accent = accent
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        diameter = min(self.width(), self.height() - 22) - 8
        rect = QRectF((self.width() - diameter) / 2, 4, diameter, diameter)
        width = max(5.0, diameter / 11)
        inner = rect.adjusted(width / 2, width / 2, -width / 2, -width / 2)

        track = QPen(QColor("#1e2a44"), width)
        track.setCapStyle(Qt.RoundCap)
        painter.setPen(track)
        painter.drawArc(inner, 225 * 16, -270 * 16)
        if self.percent is not None and self.percent > 0:
            pen = QPen(QColor(level_colour(self.percent, self.accent, more_is_better=self.more_is_better)), width)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawArc(inner, 225 * 16, int(-270 * 16 * self.percent / 100))

        value_font = QFont("Segoe UI", max(9, int(diameter / 6.5)))
        value_font.setWeight(QFont.DemiBold)
        painter.setFont(value_font)
        painter.setPen(QColor("#e2e8f0"))
        painter.drawText(rect, Qt.AlignCenter, self.text)

        caption_font = QFont("Segoe UI", 8)
        caption_font.setWeight(QFont.DemiBold)
        caption_font.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        painter.setFont(caption_font)
        painter.setPen(QColor("#8b9bb4"))
        painter.drawText(QRectF(0, rect.bottom() + 2, self.width(), 18), Qt.AlignHCenter | Qt.AlignTop,
                         self.caption.upper())
        painter.end()
