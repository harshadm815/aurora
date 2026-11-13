"""
Starting things for the Wake Engine.

    wake_command_centre()   ask the running Command Centre to wake up, or start it with --wake
    start_helper()          start the background helper (if it isn't running)
    stop_helper()           ask it to quit
    helper_running()
    set_helper_startup()    start the helper when you sign in to Windows (your choice, in Settings)

The helper has its own per-user local socket (like the Command Centre's),
so only one runs and the Command Centre can tell it to quit or reload.
"""

import getpass
import logging
import subprocess

from aurora.interface.windows import integration

logger = logging.getLogger(__name__)

QUIT, RELOAD, PING = b"quit", b"reload", b"ping"
RUN_VALUE = "AURORA Wake"
DETACHED = 0x00000008 | 0x00000200            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def helper_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "AURORA-Wake-" + ("".join(ch for ch in user if ch.isalnum()) or "user")


def _send(name: str, message: bytes, timeout_ms: int = 500) -> bool:
    from PySide6.QtNetwork import QLocalSocket

    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(message)
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True


def helper_command() -> list[str]:
    program, arguments, _ = integration.launch_command()
    return [program, *arguments, "--wake-helper"]


def wake_command_centre(start=subprocess.Popen) -> str:
    """"woken" if a running Command Centre was asked to wake up, else "started" (launched with --wake)."""
    from aurora.interface.windows.single_instance import WAKE, SingleInstance

    if SingleInstance.send(WAKE):
        logger.info("Wake: asked the running Command Centre to wake up")
        return "woken"
    program, arguments, folder = integration.launch_command()
    start([program, *arguments, "--wake"], cwd=folder, creationflags=DETACHED if integration.IS_WINDOWS else 0)
    logger.info("Wake: started the Command Centre")
    return "started"


def helper_running() -> bool:
    return _send(helper_name(), PING, 300)


def start_helper(start=subprocess.Popen) -> bool:
    """Starts the helper unless it's running. True if it was started now."""
    if helper_running():
        return False
    _, _, folder = integration.launch_command()
    start(helper_command(), cwd=folder, creationflags=DETACHED if integration.IS_WINDOWS else 0)
    logger.info("Wake: helper started")
    return True


def stop_helper() -> bool:
    return _send(helper_name(), QUIT)


def reload_helper() -> bool:
    """Asks a running helper to read its settings again."""
    return _send(helper_name(), RELOAD)


def helper_startup_enabled(registry=None) -> bool:
    if not integration.IS_WINDOWS and registry is None:
        return False
    winreg = registry or integration._winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, integration.RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_helper_startup(enabled: bool, registry=None) -> None:
    """Adds or removes the helper from your user's startup programs."""
    winreg = registry or integration._winreg()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, integration.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            command = helper_command()
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, integration.command_line(command[0], command[1:]))
            logger.info("Wake: helper starts with Windows")
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
            logger.info("Wake: helper no longer starts with Windows")
