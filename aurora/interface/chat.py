"""Conversation with AURORA through the existing engine."""

from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import clear_layout, label, safe_markdown


class MessageInput(QPlainTextEdit):
    """Enter sends; Shift+Enter starts a new line."""

    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not event.modifiers() & Qt.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class Bubble(QFrame):
    """One message. AURORA's replies are rendered as Markdown (HTML escaped)."""

    def __init__(self, who: str, text: str, kind: str = "aurora", parent=None):
        super().__init__(parent)
        self.setObjectName({"user": "UserBubble", "aurora": "AuroraBubble", "error": "ErrorBubble"}[kind])
        self.kind = kind
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(4)
        self.meta = label(f"{who} · {datetime.now():%H:%M}", "BubbleMeta")
        layout.addWidget(self.meta)
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.text.setOpenExternalLinks(False)
        self.text.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        layout.addWidget(self.text)
        self.set_text(text)

    def set_text(self, text: str):
        if self.kind == "user":
            self.text.setTextFormat(Qt.PlainText)
            self.text.setText(text)
        else:
            self.text.setTextFormat(Qt.MarkdownText)
            # Keep line breaks from tool replies (lists, multi-line answers).
            self.text.setText(safe_markdown(text).replace("\n", "  \n"))
        self.raw = text


class ChatView(BaseView):
    title = "Chat"
    subtitle = "Talk to AURORA. Enter sends, Shift+Enter adds a line."
    nav_icon = "💬"
    scrollable = False

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.busy = False
        self._streaming_bubble: Bubble | None = None

        # Messages
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self.messages = QVBoxLayout(holder)
        self.messages.setContentsMargins(4, 4, 12, 4)
        self.messages.setSpacing(10)
        self.messages.addStretch(1)
        self.scroll.setWidget(holder)
        self.body.addWidget(self.scroll, 1)

        self._add_bubble("AURORA", "Hello. Ask me anything, or try *what is 12 × 7*, "
                                   "*remember that my exam is on Friday*, or *find recent papers on exoplanets*.")

        # Thinking indicator and confirmation buttons
        self.thinking = label("", "Dim")
        self.body.addWidget(self.thinking)
        self._dots = 0
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._animate_thinking)

        self.confirm_row = QWidget()
        confirm = QHBoxLayout(self.confirm_row)
        confirm.setContentsMargins(0, 0, 0, 0)
        confirm.addWidget(label("AURORA is waiting for your answer:", "Dim"))
        self.yes_button = QPushButton("Yes")
        self.yes_button.setObjectName("Primary")
        self.no_button = QPushButton("No")
        self.yes_button.clicked.connect(lambda: self.send("yes"))
        self.no_button.clicked.connect(lambda: self.send("no"))
        confirm.addWidget(self.yes_button)
        confirm.addWidget(self.no_button)
        confirm.addStretch(1)
        self.confirm_row.hide()
        self.body.addWidget(self.confirm_row)

        # Input
        row = QHBoxLayout()
        self.input = MessageInput()
        self.input.setPlaceholderText("Message AURORA…")
        self.input.setFixedHeight(64)
        self.input.submitted.connect(lambda: self.send())
        row.addWidget(self.input, 1)
        buttons = QVBoxLayout()
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("Primary")
        self.send_button.clicked.connect(lambda: self.send())
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Start a new conversation (memories are kept)")
        self.clear_button.clicked.connect(self.clear)
        buttons.addWidget(self.send_button)
        buttons.addWidget(self.clear_button)
        row.addLayout(buttons)
        self.body.addLayout(row)

        self._set_ready(self.backend.engine is not None)

    # --- Messages -----------------------------------------------------------------

    def _add_bubble(self, who: str, text: str, kind: str = "aurora") -> Bubble:
        bubble = Bubble(who, text, kind)
        line = QHBoxLayout()
        if kind == "user":
            line.addStretch(1)
            line.addWidget(bubble, 6)
        else:
            line.addWidget(bubble, 6)
            line.addStretch(1)
        self.messages.insertLayout(self.messages.count() - 1, line)
        QTimer.singleShot(30, self._scroll_to_end)
        return bubble

    def _scroll_to_end(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _set_ready(self, ready: bool):
        self.send_button.setEnabled(ready and not self.busy)
        self.input.setEnabled(ready)
        if not ready:
            self.thinking.setText("AURORA is starting…")
        elif not self.busy:
            self.thinking.setText("")

    def on_engine_ready(self):
        self._set_ready(True)

    def on_shown(self):
        self.input.setFocus()

    # --- Sending ------------------------------------------------------------------

    def send(self, text: str | None = None):
        if self.busy:
            return
        text = (text if text is not None else self.input.toPlainText()).strip()
        if not text:
            return
        self.input.clear()
        self.confirm_row.hide()
        self._add_bubble("You", text, "user")

        self.busy = True
        self._streaming_bubble = None
        self._set_ready(True)
        self._dot_timer.start(400)
        self._animate_thinking()
        self.backend.send_message(text, self._on_reply, self._on_error, on_token=self._on_token)

    def _animate_thinking(self):
        self._dots = (self._dots % 3) + 1
        self.thinking.setText("AURORA is thinking" + "." * self._dots)

    def _on_token(self, piece: str):
        if self._streaming_bubble is None:
            self._streaming_bubble = self._add_bubble("AURORA", piece)
            self._streaming_bubble.raw = piece
        else:
            self._streaming_bubble.set_text(self._streaming_bubble.raw + piece)
        self._scroll_to_end()

    def _finish(self):
        self.busy = False
        self._dot_timer.stop()
        self.thinking.setText("")
        self._set_ready(True)
        self.input.setFocus()

    def _on_reply(self, result):
        reply, needs_confirmation = result
        if self._streaming_bubble is not None:
            # The final text may add notes (e.g. "Remembered …") after the stream.
            self._streaming_bubble.set_text(reply)
        else:
            self._add_bubble("AURORA", reply)
        self._streaming_bubble = None
        self.confirm_row.setVisible(needs_confirmation)
        self._finish()

    def _on_error(self, error):
        self._streaming_bubble = None
        self._add_bubble("AURORA", f"Something went wrong: {error}", "error")
        self._finish()

    def clear(self):
        if self.busy:
            return
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            if item.layout() is not None:
                clear_layout(item.layout())
        self.confirm_row.hide()
        self.backend.clear_conversation()
        self._add_bubble("AURORA", "Conversation cleared. Your memories are kept.")
