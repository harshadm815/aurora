"""Shared building blocks for the Command Centre's views."""

import html
import math

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget,
)

STATE_COLOURS = {
    "ok": "#34d399", "warning": "#fbbf24", "error": "#f87171",
    "info": "#60a5fa", "off": "#5b6b85", "simulated": "#a78bfa",
}


def discard(widget: QWidget) -> None:
    """Removes a widget from view at once (deleteLater alone can leave it visible)."""
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            discard(item.widget())
        elif item.layout() is not None:
            clear_layout(item.layout())


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def safe_markdown(text: str) -> str:
    """
    Markdown for display. HTML in the text (which may come from web pages
    or files) is escaped, so it shows as text instead of being rendered.
    """
    return html.escape(text, quote=False)


class Panel(QFrame):
    """A rounded panel with an optional small-caps title."""

    def __init__(self, title: str | None = None, parent=None, card: bool = False):
        super().__init__(parent)
        self.setObjectName("Card" if card else "Panel")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(18, 16, 18, 16)
        self.layout_.setSpacing(10)
        if title:
            self.layout_.addWidget(label(title.upper(), "SectionTitle"))

    def add(self, widget, stretch: int = 0):
        self.layout_.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout):
        self.layout_.addLayout(layout)
        return layout


class StatusDot(QWidget):
    """A small coloured dot that gently pulses for 'live' states."""

    def __init__(self, state: str = "off", pulse: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._state = state
        self._pulse = pulse
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if pulse:
            self._timer.start(60)

    def set_state(self, state: str, pulse: bool | None = None):
        self._state = state
        if pulse is not None and pulse != self._pulse:
            self._pulse = pulse
            self._timer.start(60) if pulse else self._timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.12) % (2 * math.pi)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = QColor(STATE_COLOURS.get(self._state, STATE_COLOURS["off"]))
        if self._pulse:
            glow = QColor(colour)
            glow.setAlpha(int(60 + 50 * math.sin(self._phase)))
            painter.setBrush(glow)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(0, 0, 12, 12))
        painter.setBrush(colour)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(3, 3, 6, 6))


class StatusRow(QWidget):
    """'● Name ........ State' line."""

    def __init__(self, name: str, state_text: str = "", state: str = "off", parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        self.dot = StatusDot(state)
        self.name = label(name)
        self.value = label(state_text, "Dim")
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.dot)
        layout.addWidget(self.name)
        layout.addStretch(1)
        layout.addWidget(self.value)

    def set(self, state_text: str, state: str):
        self.value.setText(state_text)
        self.dot.set_state(state)


class StatCard(Panel):
    """A titled card with one big value, a detail line and an optional meter."""

    def __init__(self, title: str, meter: bool = False, parent=None):
        super().__init__(title, parent, card=True)
        self.value = self.add(label("—", "BigValue"))
        self.detail = self.add(label("", "Faint", wrap=True))
        self.meter = None
        if meter:
            self.meter = QProgressBar()
            self.meter.setRange(0, 100)
            self.meter.setTextVisible(False)
            self.meter.setFixedHeight(8)
            self.add(self.meter)

    def set(self, value: str, detail: str = "", percent: float | None = None):
        self.value.setText(value)
        self.detail.setText(detail)
        if self.meter is not None:
            self.meter.setValue(int(percent) if percent is not None else 0)
            self.meter.setEnabled(percent is not None)


class Badge(QLabel):
    """A small rounded tag, e.g. SIMULATED, LIVE, READY."""

    def __init__(self, text: str = "", state: str = "info", parent=None):
        super().__init__(parent)
        self.set(text, state)

    def set(self, text: str, state: str):
        colour = STATE_COLOURS.get(state, STATE_COLOURS["info"])
        self.setText(text)
        self.setStyleSheet(
            f"color: {colour}; border: 1px solid {colour}; border-radius: 8px; "
            "padding: 1px 8px; font-size: 8pt; font-weight: 600;"
        )
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)


def page_header(title: str, subtitle: str = "") -> QWidget:
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 8)
    layout.setSpacing(2)
    layout.addWidget(label(title, "PageTitle"))
    if subtitle:
        layout.addWidget(label(subtitle, "PageSubtitle", wrap=True))
    return widget


def fade_in(widget: QWidget, duration: int = 220) -> None:
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    # Remove the effect afterwards: it slows painting of complex widgets.
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start(QPropertyAnimation.DeleteWhenStopped)


class Emblem(QWidget):
    """AURORA's emblem: concentric arcs around a core, drawn (no image files)."""

    def __init__(self, size: int = 44, accent: str = "#22d3ee", parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.accent = QColor(accent)

    def set_accent(self, accent: str):
        self.accent = QColor(accent)
        self.update()

    def paintEvent(self, event):
        draw_emblem(QPainter(self), self.width(), self.accent)


def draw_emblem(painter: QPainter, size: int, accent: QColor) -> None:
    painter.setRenderHint(QPainter.Antialiasing)
    centre = QPointF(size / 2, size / 2)
    for radius, start, span, alpha, width in [
        (size * 0.46, 30, 300, 255, max(1.5, size / 22)),
        (size * 0.34, 210, 250, 170, max(1.2, size / 30)),
        (size * 0.22, 100, 280, 120, max(1.0, size / 36)),
    ]:
        colour = QColor(accent)
        colour.setAlpha(alpha)
        pen = QPen(colour, width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius),
                        start * 16, span * 16)
    core = QColor(accent)
    painter.setPen(Qt.NoPen)
    painter.setBrush(core)
    painter.drawEllipse(centre, size * 0.07, size * 0.07)


def emblem_pixmap(size: int, accent: str) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    draw_emblem(painter, size, QColor(accent))
    painter.end()
    return pixmap


def title_font(size: int = 15, weight: QFont.Weight = QFont.DemiBold) -> QFont:
    font = QFont("Segoe UI", size)
    font.setWeight(weight)
    return font
