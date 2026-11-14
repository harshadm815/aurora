"""
Only one Command Centre per Windows user.

The first instance listens on a named local socket. A second launch
(another click on the shortcut, the startup entry, …) connects to it,
asks it to show its window, and exits, so AURORA never runs twice
and never starts duplicate background workers.

Two requests, each with its own socket name, so the request is the
connection itself and no message has to be read (reading one proved
timing-dependent):

    <name>          show the window
    <name>-wake     the Wake Engine: show, greet and listen
"""

import getpass
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

SHOW = b"show"
WAKE = b"wake"
WAKE_SUFFIX = "-wake"


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
        self.wake_server: QLocalServer | None = None

    def _connect(self, name: str, timeout_ms: int) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(name)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.disconnectFromServer()
        return True

    def _signal_running(self, timeout_ms: int, message: bytes = SHOW) -> bool:
        if message == WAKE:
            if self._connect(self.name + WAKE_SUFFIX, timeout_ms):
                return True
            # A Command Centre without a wake socket (or none at all): at least show it.
        return self._connect(self.name, timeout_ms)

    def _listen(self, name: str, handler) -> QLocalServer | None:
        server = QLocalServer(self)
        if not server.listen(name):
            # A crash can leave a stale name behind; nobody answered, so remove it.
            QLocalServer.removeServer(name)
            if not server.listen(name):
                logger.warning("Local socket %s unavailable: %s", name, server.errorString())
                return None
        server.newConnection.connect(lambda: self._on_connection(server, handler))
        return server

    def acquire(self, timeout_ms: int = 500, message: bytes = SHOW) -> bool:
        if self._signal_running(timeout_ms, message):
            logger.info("AURORA is already running; asked it to %s", message.decode(errors="replace"))
            return False
        self.server = self._listen(self.name, self.activated.emit)
        self.wake_server = self._listen(self.name + WAKE_SUFFIX, self.woken.emit)
        return True

    @staticmethod
    def _on_connection(server: QLocalServer, handler):
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
            handler()

    @staticmethod
    def send(message: bytes, name: str | None = None, timeout_ms: int = 500) -> bool:
        """Asks the running Command Centre to show itself (SHOW) or wake up (WAKE); False if none runs."""
        return SingleInstance(name)._signal_running(timeout_ms, message)

    def release(self):
        for server in (self.server, self.wake_server):
            if server is not None:
                server.close()
        self.server = self.wake_server = None
