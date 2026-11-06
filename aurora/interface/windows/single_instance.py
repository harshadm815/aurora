"""
Only one Command Centre per Windows user.

The first instance listens on a named local socket. A second launch
(another click on the shortcut, the startup entry, …) connects to it,
asks it to show its window, and exits, so AURORA never runs twice
and never starts duplicate background workers.
"""

import getpass
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

SHOW = b"show"


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

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self.name = name or server_name()
        self.server: QLocalServer | None = None

    def _signal_running(self, timeout_ms: int) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(SHOW)
        socket.flush()
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True

    def acquire(self, timeout_ms: int = 500) -> bool:
        if self._signal_running(timeout_ms):
            logger.info("AURORA is already running; asked it to show itself")
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
        # Only another AURORA launch connects to this per-user socket, and it
        # may disconnect before its message is read, so any connection means
        # "show the window".
        while self.server is not None and self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
            self.activated.emit()

    def release(self):
        if self.server is not None:
            self.server.close()
            self.server = None
