"""
Only one Command Centre per Windows user.

The first instance listens on a named local socket. A second launch
(another click on the shortcut, the startup entry, …) connects to it,
asks it to show its window, and exits, so AURORA never runs twice
and never starts duplicate background workers.

Messages: "show" (bring the window forward) and "wake" (the Wake Engine:
bring it forward, greet and listen). Anything else, or nothing, means show.
"""

import getpass
import logging

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

SHOW = b"show"
WAKE = b"wake"


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    safe = "".join(ch for ch in user if ch.isalnum()) or "user"
    return f"AURORA-CommandCentre-{safe}"


class SingleInstance(QObject):
    """``acquire()`` returns True for the first instance, False if one is already running."""

    activated = Signal()   # Another launch asked this instance to show itself.
    woken = Signal()       # The Wake Engine asked AURORA to wake up (show, greet, listen).

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self.name = name or server_name()
        self.server: QLocalServer | None = None

    def _signal_running(self, timeout_ms: int, message: bytes = SHOW) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(message)
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        # Let the running instance read the message and hang up first: disconnecting
        # straight away can drop it (and "wake" would be taken for "show").
        if not socket.waitForDisconnected(timeout_ms):
            socket.disconnectFromServer()
        return True

    def acquire(self, timeout_ms: int = 500, message: bytes = SHOW) -> bool:
        if self._signal_running(timeout_ms, message):
            logger.info("AURORA is already running; asked it to %s", message.decode(errors="replace"))
            return False

        self.server = QLocalServer(self)
        if not self.server.listen(self.name):
            # A crash can leave a stale name behind; nobody answered above, so remove it.
            QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):
                logger.warning("Single-instance guard unavailable: %s", self.server.errorString())
                return True
        self.server.newConnection.connect(self._on_connection)
        return True

    def _on_connection(self):
        # Only another AURORA launch connects to this per-user socket. It may
        # disconnect before its message is read; anything but "wake" means
        # "show the window".
        while self.server is not None and self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            handled = []

            def handle(socket=socket, handled=handled):
                if handled:
                    return
                handled.append(True)
                message = bytes(socket.readAll().data()).strip()
                socket.disconnectFromServer()
                socket.deleteLater()
                if message == WAKE:
                    self.woken.emit()
                else:
                    self.activated.emit()

            # Read the message when it arrives (or treat the connection as "show" after half a second).
            socket.readyRead.connect(handle)
            QTimer.singleShot(500, handle)
            if socket.bytesAvailable():
                handle()

    @staticmethod
    def send(message: bytes, name: str | None = None, timeout_ms: int = 500) -> bool:
        """Sends a message to the running Command Centre; False if none is running."""
        return SingleInstance(name)._signal_running(timeout_ms, message)

    def release(self):
        if self.server is not None:
            self.server.close()
            self.server = None
