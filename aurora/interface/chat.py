"""Conversation with AURORA through the existing engine."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)

from aurora.config import settings
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
        # Where the reply came from: documents, memory, live sources, the language model…
        self.sources = label("", "BubbleMeta", wrap=True)
        self.sources.hide()
        layout.addWidget(self.sources)
        self.set_text(text)

    def set_sources(self, text: str):
        self.sources.setText(text)
        self.sources.setVisible(bool(text))

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

        # Voice: push to talk, stop, spoken replies
        voice_row = QHBoxLayout()
        self.mic_button = QPushButton("🎙  Ask by voice")
        self.mic_button.setToolTip("Opens the microphone for one question. It closes again when you stop talking.")
        self.mic_button.clicked.connect(self.listen)
        self.stop_voice_button = QPushButton("■  Stop")
        self.stop_voice_button.setToolTip("Stop listening or speaking now.")
        self.stop_voice_button.clicked.connect(self.backend.voice.stop)
        self.stop_voice_button.setEnabled(False)
        self.speak_replies = QCheckBox("Speak replies")
        self.speak_replies.setChecked(bool(self.backend.preferences.get("speak_replies", False)))
        self.speak_replies.toggled.connect(lambda on: self.backend.save_preferences({"speak_replies": on}))
        self.voice_status = label("", "Faint")
        self.image_button = QPushButton("🖼  Image…")
        self.image_button.setToolTip("Choose a picture for AURORA to describe (analysed on this laptop, not saved).")
        self.image_button.clicked.connect(lambda: self.ask_image())
        voice_row.addWidget(self.image_button)
        voice_row.addWidget(self.mic_button)
        voice_row.addWidget(self.stop_voice_button)
        voice_row.addWidget(self.speak_replies)
        voice_row.addWidget(self.voice_status, 1)
        self.body.addLayout(voice_row)
        backend.voice.state_changed.connect(self._voice_state)
        backend.voice.level.connect(self._voice_level)

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
        backend.state_changed.connect(lambda _: self._set_ready(self.backend.engine is not None))

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
        paused = self.backend.state == "paused"
        ready = ready and self.backend.state == "running"
        self.send_button.setEnabled(ready and not self.busy)
        self.input.setEnabled(ready)
        if paused:
            self.thinking.setText("AURORA is paused. Start it from the Dashboard or the tray menu.")
        elif not ready:
            self.thinking.setText("AURORA is starting…")
        elif not self.busy:
            self.thinking.setText("")

    def on_engine_ready(self):
        self._set_ready(True)

    def on_shown(self):
        self.input.setFocus()

    # --- Sending ------------------------------------------------------------------

    # --- Voice --------------------------------------------------------------------

    def listen(self):
        if self.busy or self.backend.voice.state in ("listening", "loading"):
            return
        self.backend.voice.stop()        # stop a reply that's still being spoken
        self.voice_status.setText("Getting the microphone ready…")
        self.backend.voice.listen(self._heard, self._voice_failed)

    def _heard(self, text: str):
        if not text:
            self.voice_status.setText("I didn't hear anything. Try again, a little closer to the microphone.")
            return
        self.voice_status.setText("")
        self.send(text, by_voice=True)

    def _voice_failed(self, error):
        self.voice_status.setText(f"Voice isn't available: {error}")

    def _voice_state(self, state: str):
        speaking_or_listening = state in ("listening", "speaking")
        self.stop_voice_button.setEnabled(speaking_or_listening)
        self.mic_button.setEnabled(state not in ("listening", "loading"))
        messages = {"loading": "Loading the speech model (the first time takes a while)…",
                    "listening": "🎙  Listening… speak now", "speaking": "🔊  Speaking… (Stop to interrupt)",
                    "unavailable": f"Voice isn't available: {self.backend.voice.problem}"}
        if state in messages:
            self.voice_status.setText(messages[state])
        elif self.voice_status.text().startswith(("🎙", "🔊", "Loading")):
            self.voice_status.setText("")

    def _voice_level(self, rms: float, speaking: bool):
        bars = min(10, int(rms / 300))
        self.voice_status.setText(("🎙  Hearing you  " if speaking else "🎙  Listening… speak now  ") + "▮" * bars)

    def send(self, text: str | None = None, by_voice: bool = False):
        if self.busy:
            return
        text = (text if text is not None else self.input.toPlainText()).strip()
        if not text:
            return
        self.input.clear()
        self.confirm_row.hide()
        self._add_bubble("You (voice)" if by_voice else "You", text, "user")

        self.busy = True
        self._streaming_bubble = None
        self._set_ready(True)
        self._dot_timer.start(400)
        self._animate_thinking()
        self.backend.send_message(text, self._on_reply, self._on_error, on_token=self._on_token)

    def ask_image(self, path: Path | None = None):
        """Describes a picture the user chooses, with its evidence."""
        if self.busy:
            return
        if path is None:
            from aurora.vision.image_input import FILE_FILTER

            start = str(settings.DOCUMENTS_DIR) if settings.DOCUMENTS_DIR.is_dir() else ""
            chosen, _ = QFileDialog.getOpenFileName(self, "Choose a picture", start, FILE_FILTER)
            if not chosen:
                return
            path = Path(chosen)
        self._add_bubble("You", f"🖼  What's in {path.name}?", "user")
        self.busy = True
        self._set_ready(True)
        self.thinking.setText("Looking at the picture… (the first time takes a few seconds)")
        self.backend.run_io(lambda: self.backend.describe_image(path), self._on_image_report, self._on_error)

    def _on_image_report(self, report):
        bubble = self._add_bubble("AURORA", report.message(heading=report.name))
        from aurora.knowledge.sources import DOCUMENT, Source, caption

        bubble.set_sources(caption([Source(DOCUMENT, report.name, "image, analysed on this computer")]))
        self._finish()
        if self.speak_replies.isChecked():
            self.backend.voice.speak(report.description())

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
        reply, needs_confirmation, *rest = result
        sources = rest[0] if rest else ""
        if self._streaming_bubble is not None:
            # The final text may add notes (e.g. "Remembered …") after the stream.
            self._streaming_bubble.set_text(reply)
            bubble = self._streaming_bubble
        else:
            bubble = self._add_bubble("AURORA", reply)
        bubble.set_sources(sources)
        self._streaming_bubble = None
        self.confirm_row.setVisible(needs_confirmation)
        self._finish()
        if self.speak_replies.isChecked():
            self.backend.voice.speak(reply)

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
