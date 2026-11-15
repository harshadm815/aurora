"""
The phone helper: lets your paired phone start AURORA while it's closed.

``AURORA Command Centre.exe --phone-helper`` (no window, small) runs while
the Command Centre is closed, if you switched on "Let my phone start AURORA"
on the Phone page: at Windows sign-in, and again whenever AURORA quits. It
listens exactly where AURORA would (the cable and Tailscale on 127.0.0.1, the
home Wi-Fi with AURORA's certificate) with the same checks: only the paired
phone, every request signed (server.py, "closed" mode). It answers only:

    GET  /api/status       {"state": "closed", ...}
    GET  /api/connection   as AURORA would
    POST /api/control      {"action": "start"} → starts AURORA (in the notification area) and quits

Everything else is refused with "AURORA is closed on your PC". The Command
Centre asks the helper to quit when it starts (it needs the same ports), and
starts it again when it quits.
"""

import getpass
import logging
import subprocess
import threading
import time
from datetime import datetime

from aurora.config import settings

logger = logging.getLogger(__name__)

QUIT, PING = b"quit", b"ping"
RUN_VALUE = "AURORA Phone"
DETACHED = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
BIND_TRIES = 30                                # AURORA may still be letting go of the ports


def helper_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "AURORA-Phone-" + ("".join(ch for ch in user if ch.isalnum()) or "user")


def _send(message: bytes, timeout_ms: int = 500) -> bool:
    from aurora.wake.launcher import _send as send
    return send(helper_name(), message, timeout_ms)


def helper_command() -> list[str]:
    from aurora.interface.windows import integration
    program, arguments, _ = integration.launch_command()
    return [program, *arguments, "--phone-helper"]


def helper_running() -> bool:
    return _send(PING, 300)


def start_helper(start=subprocess.Popen) -> bool:
    """Starts the helper unless it's running. True if started now."""
    from aurora.interface.windows import integration
    if helper_running():
        return False
    _, _, folder = integration.launch_command()
    start(helper_command(), cwd=folder, creationflags=DETACHED if integration.IS_WINDOWS else 0)
    logger.info("Phone helper started")
    return True


def stop_helper() -> bool:
    return _send(QUIT)


def set_helper_startup(enabled: bool, registry=None) -> None:
    """Starts the helper when you sign in to Windows (your choice on the Phone page), or stops doing so."""
    from aurora.interface.windows import integration
    winreg = registry or integration._winreg()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, integration.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = helper_command()
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, integration.command_line(command[0], command[1:]))
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
    logger.info("Phone helper %s with Windows", "starts" if enabled else "no longer starts")


class NoSpeech:
    """The helper never hears or speaks (calls need AURORA itself)."""

    def release_if_idle(self):
        pass

    def warm(self):
        pass


class ClosedBridge:
    """What the helper can tell the phone: AURORA is closed; and it can start it."""

    closed = True

    def __init__(self, on_start):
        self.on_start = on_start

    def status(self) -> dict:
        return {"ai": "closed", "state": "closed", "model": "", "cpu": None, "ram": None,
                "version": settings.VERSION, "time": datetime.now().isoformat(timespec="seconds"),
                "services": []}

    def control(self, action: str) -> str:
        if action != "start":
            raise ValueError(action)
        self.on_start()
        return "Starting AURORA on your PC. It's ready in about half a minute."


class PhoneHelper:
    """Runs the closed-mode service until the phone asks for AURORA (or the Command Centre says quit)."""

    def __init__(self, preferences: dict, service_factory=None, launch=None, quit=None):
        self.preferences = preferences
        self.service_factory = service_factory
        self.launch = launch or self._launch
        self.quit = quit or (lambda: None)
        self.service = None
        self._starting = threading.Lock()

    def start(self, tries: int = BIND_TRIES, pause: float = 1.0) -> bool:
        from aurora.mobile.service import MobileService
        for attempt in range(tries):
            try:
                factory = self.service_factory or (lambda bridge: MobileService(
                    bridge, wifi=bool(self.preferences.get("mobile_wifi")),
                    home_networks=self.preferences.get("mobile_home_networks") or [], speech=NoSpeech()))
                self.service = factory(ClosedBridge(self.start_aurora))
                self.service.start()
                logger.info("Phone helper: waiting for the phone")
                return True
            except OSError:
                if self.service is not None:
                    self.service.stop()
                    self.service = None
                time.sleep(pause)                       # AURORA is still closing
        logger.warning("Phone helper: the phone's ports stayed busy")
        return False

    def start_aurora(self) -> None:
        """Called on the request's thread: answer first, then let go of the ports, start AURORA, quit."""
        if not self._starting.acquire(blocking=False):
            return

        def later():
            time.sleep(0.5)
            self.stop()
            try:
                self.launch()
            finally:
                self.quit()

        threading.Thread(target=later, name="aurora-phone-start", daemon=True).start()

    @staticmethod
    def _launch() -> None:
        from aurora.interface.windows import integration
        program, arguments, folder = integration.launch_command(minimized=True)
        subprocess.Popen([program, *arguments], cwd=folder, creationflags=DETACHED if integration.IS_WINDOWS else 0)
        logger.info("Phone helper: AURORA started for the phone")

    def stop(self) -> None:
        if self.service is not None:
            self.service.stop()
            self.service = None


def main(arguments: list[str] | None = None) -> int:
    """``--phone-helper``: one per Windows user, until the phone starts AURORA or AURORA says quit."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalServer

    from aurora.config import preferences
    from aurora.utils.log import setup_logging

    setup_logging()
    app = QCoreApplication([])
    server = QLocalServer()
    name = helper_name()
    if not server.listen(name):
        if _send(PING, 300):
            logger.info("Phone helper: already running")
            return 0
        QLocalServer.removeServer(name)
        server.listen(name)

    def command():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if not socket.bytesAvailable():
                socket.waitForReadyRead(200)
            message = bytes(socket.readAll().data()).strip()
            socket.disconnectFromServer()
            if message == QUIT:
                app.quit()

    server.newConnection.connect(command)
    helper = PhoneHelper(preferences.load(), quit=lambda: QCoreApplication.instance().quit())
    if not helper.start():
        return 1
    app.aboutToQuit.connect(helper.stop)
    code = app.exec()
    logger.info("Phone helper stopped")
    return code
