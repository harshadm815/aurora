"""
The Wake Engine's background helper: ``AURORA Command Centre.exe --wake-helper``.

While idle it only watches for the activation key: no microphone, no
speech model, no window (about 45 MB of memory, no measurable CPU).

    hold the key        → a short beep, a small "Listening for 'Wake up'" panel
                          above the taskbar, the microphone opens
    say the phrase      → the microphone closes at once, "Waking AURORA…",
                          the Command Centre comes forward (or starts) and greets you
    release the key     → the microphone closes; what was said while it was held
                          is still checked, then the panel goes away
    press another key   → cancelled
    hold for 8 s        → the microphone closes anyway

Only one helper runs per Windows user; the Command Centre can tell it to
quit or to read its settings again (local socket, see launcher.py). It
needs the Voice component's 'microphone' permission, like every other
use of the microphone. Nothing heard is stored or logged.
"""

import logging
import math
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QProgressBar, QVBoxLayout

from aurora.config import preferences, settings
from aurora.wake import launcher
from aurora.wake.detector import HEARD, UNKNOWN_WORDS, ModelCache, WakeSession, phrase_words
from aurora.wake.keys import ACTIVE, IDLE, LABELS, PENDING, HoldKey

logger = logging.getLogger(__name__)

RATE = 16000
BLOCK = 1600                   # 0.1 s of audio per microphone callback


def microphone_allowed() -> bool:
    from aurora.kernel.permissions import PermissionManager
    from aurora.voice.session import COMPONENT, PERMISSIONS

    return not PermissionManager(settings.PERMISSION_GRANTS).missing(COMPONENT, PERMISSIONS)


class Microphone:
    """Opens the microphone (16 kHz mono) and hands each 0.1 s block to ``on_audio(bytes)``."""

    def __init__(self, on_audio):
        import sounddevice

        self._stream = sounddevice.RawInputStream(
            samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, device=settings.VOICE_INPUT_DEVICE,
            callback=lambda data, frames, when, status: on_audio(bytes(data)))
        self._stream.start()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


class Overlay(QFrame):
    """The small panel above the taskbar that shows the microphone is on."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("WakeOverlay")
        self.setStyleSheet("""
            #WakeOverlay { background: #0b1220; border: 1px solid #22d3ee; border-radius: 12px; }
            QLabel { color: #e2e8f0; font: 11pt 'Segoe UI'; }
            QProgressBar { background: #1e293b; border: none; border-radius: 3px; max-height: 6px; }
            QProgressBar::chunk { background: #22d3ee; border-radius: 3px; }""")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        self.text = QLabel()
        self.text.setAlignment(Qt.AlignCenter)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        layout.addWidget(self.text)
        layout.addWidget(self.level)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self.hide)

    def say(self, text: str, listening: bool = False, hide_after: float | None = None) -> None:
        self.text.setText(text)
        self.level.setVisible(listening)
        self.level.setValue(0)
        self.adjustSize()
        self.setFixedWidth(max(320, self.sizeHint().width()))
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.bottom() - self.height() - 24)
        self.show()
        self.raise_()
        if hide_after:
            self._hide_timer.start(int(hide_after * 1000))
        else:
            self._hide_timer.stop()


class WakeHelper(QObject):

    result_ready = Signal(str)        # from the detector's thread
    level_ready = Signal(float)       # from the microphone's thread

    def __init__(self, hook_factory=None, microphone_factory=Microphone, wake=launcher.wake_command_centre,
                 cache: ModelCache | None = None, beep=None, clock=time.monotonic):
        super().__init__()
        self.hook_factory = hook_factory
        self.microphone_factory = microphone_factory
        self.wake_aurora = wake
        self.cache = cache or ModelCache()
        self.beep = beep or _beep
        self.clock = clock
        self.overlay = Overlay()
        self.microphone = None
        self.session: WakeSession | None = None
        self.machine: HoldKey | None = None
        self.hook = None
        self.listening_since = 0.0
        self.result_ready.connect(self._result)
        self.level_ready.connect(lambda rms: self.overlay.level.setValue(min(100, int(rms / 30))))
        self._ticker = QTimer(self, interval=50, timeout=self._tick)          # only while the key is down
        self._tidy = QTimer(self, interval=60_000, timeout=self.cache.release_if_idle)
        self._tidy.start()
        self.server = None
        self.configure()

    # -------------------------------------------------------------- set-up

    def configure(self) -> None:
        """(Re)reads the settings and (re)installs the keyboard hook."""
        preferences.apply(preferences.load())
        if self.hook is not None:
            self.hook.uninstall()
        self.machine = HoldKey(settings.WAKE_KEY, settings.WAKE_HOLD_SECONDS)
        if self.hook_factory is not None:
            self.hook = self.hook_factory(self.machine, self._poke)
        else:
            from aurora.wake.hook import KeyboardHook
            self.hook = KeyboardHook(self.machine, self._poke)
        self.hook.install()
        logger.info("Wake: ready (hold %s)", LABELS[settings.WAKE_KEY])

    def listen_for_commands(self, name: str | None = None) -> bool:
        """The helper's own socket: one helper per user; quit / reload / ping from the Command Centre."""
        self.server = QLocalServer(self)
        name = name or launcher.helper_name()
        if not self.server.listen(name):
            if launcher._send(name, launcher.PING, 300):
                return False                      # another helper is running
            QLocalServer.removeServer(name)
            if not self.server.listen(name):
                logger.warning("Wake: couldn't open the helper's socket")
                return True
        self.server.newConnection.connect(self._command)
        return True

    def _command(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if not socket.bytesAvailable():
                socket.waitForReadyRead(200)
            message = bytes(socket.readAll().data()).strip()
            socket.disconnectFromServer()
            socket.deleteLater()
            if message == launcher.QUIT:
                QApplication.instance().quit()
            elif message == launcher.RELOAD:
                self.configure()

    def shutdown(self) -> None:
        self._close_microphone()
        if self.session is not None:
            self.session.cancel()
        if self.hook is not None:
            self.hook.uninstall()
        logger.info("Wake: helper stopped")

    # ----------------------------------------------------------- key events

    def _poke(self) -> None:
        """Called from the keyboard hook: look at the state machine once the hook has returned."""
        QTimer.singleShot(0, self._process)

    def _process(self) -> None:
        if self.machine.state == PENDING and not self._ticker.isActive():
            self._ticker.start()
        for action in self.machine.take_actions():
            {"start": self._start, "stop": self._stop, "cancel": self._cancel}[action]()
        if self.machine.state not in (PENDING, ACTIVE):
            self._ticker.stop()

    def _tick(self) -> None:
        self.machine.tick(self.clock())
        self._process()
        if self.microphone is not None and self.clock() - self.listening_since > settings.WAKE_MAX_HOLD_SECONDS:
            self._stop()

    # ------------------------------------------------------------ listening

    def _start(self) -> None:
        if not microphone_allowed():
            self.overlay.say("AURORA isn't allowed to use the microphone (Voice → microphone).", hide_after=3)
            return
        phrase = " ".join(phrase_words(settings.WAKE_PHRASE))
        self.session = WakeSession(self.cache, phrase, RATE, self.result_ready.emit)
        try:
            self.microphone = self.microphone_factory(self._audio)
        except Exception as error:
            logger.warning("Wake: microphone unavailable (%s)", type(error).__name__)
            self.session.cancel()
            self.session = None
            self.overlay.say("The microphone isn't available.", hide_after=3)
            return
        self.listening_since = self.clock()
        if settings.WAKE_SOUND:
            self.beep()
        loading = "" if self.cache.loaded else "  (getting ready…)"
        self.overlay.say(f"🎙  Listening for “{phrase.capitalize()}”{loading}", listening=True)
        logger.info("Wake: listening (key held)")

    def _audio(self, chunk: bytes) -> None:
        session = self.session
        if session is not None:
            session.feed(chunk)
            samples = memoryview(chunk).cast("h")
            if len(samples):
                self.level_ready.emit(math.sqrt(sum(s * s for s in samples[::8]) / len(samples[::8])))

    def _close_microphone(self) -> None:
        if self.microphone is not None:
            self.microphone.close()
            self.microphone = None
            logger.info("Wake: microphone closed")

    def _stop(self) -> None:
        """Key released (or held too long): close the microphone, check what was said."""
        if self.microphone is None:
            return
        self._close_microphone()
        if self.session is not None and self.session.result is None:
            self.overlay.say("Checking…")
            self.session.finish()

    def _cancel(self) -> None:
        self._close_microphone()
        if self.session is not None:
            self.session.cancel()
            self.session = None
        self.overlay.hide()
        logger.info("Wake: cancelled (another key)")

    def _result(self, result: str) -> None:
        self._close_microphone()
        session, self.session = self.session, None
        if result == UNKNOWN_WORDS:
            words = ", ".join(session.unknown) if session is not None else ""
            self.overlay.say(f"The wake phrase has words AURORA can't recognise ({words}). "
                             "Choose another in Settings.", hide_after=4)
        elif result == HEARD:
            self.overlay.say("✦  Waking AURORA…", hide_after=2.5)
            logger.info("Wake: phrase heard")
            try:
                self.wake_aurora()
            except Exception:
                logger.exception("Wake: couldn't start AURORA")
                self.overlay.say("AURORA couldn't be started.", hide_after=3)
        else:
            self.overlay.say(f"Didn't hear “{settings.WAKE_PHRASE.capitalize()}”. Hold "
                             f"{LABELS[settings.WAKE_KEY]} and try again.", hide_after=2)

    @property
    def idle(self) -> bool:
        return self.machine.state == IDLE and self.microphone is None


def _beep() -> None:
    def play():
        try:
            import winsound
            winsound.Beep(880, 70)
        except Exception:
            pass
    threading.Thread(target=play, daemon=True).start()


def main(arguments: list[str] | None = None) -> int:
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    helper = WakeHelper()
    if not helper.listen_for_commands():
        logger.info("Wake: a helper is already running")
        helper.shutdown()
        return 0
    app.aboutToQuit.connect(helper.shutdown)
    return app.exec()
