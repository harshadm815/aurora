"""
Windows desktop integration: app identity, launch on startup, shortcuts.

- **App identity** (AppUserModelID): makes Windows show AURORA with its own
  icon in the taskbar instead of Python's, and lets you pin it.
- **Launch on startup**: a value under
  HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run (your
  user only, no admin rights). Added or removed only when you tick the
  option in Settings.
- **Desktop shortcut**: a .lnk with AURORA's icon and app identity, so the
  pinned taskbar button and the shortcut are the same app.

Windows does not allow programs to pin themselves to the taskbar; pin it
by right-clicking AURORA's taskbar button → Pin to taskbar.
"""

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_ID = "Aurora.CommandCentre"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "AURORA Command Centre"
SHORTCUT_NAME = "AURORA Command Centre.lnk"
IS_WINDOWS = sys.platform == "win32"


def set_app_id(app_id: str = APP_ID) -> bool:
    """Gives this process AURORA's own taskbar identity (call before creating windows)."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except (AttributeError, OSError):
        logger.warning("Couldn't set the Windows app identity")
        return False


# --- How to launch AURORA ---------------------------------------------------------------

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def launch_command(minimized: bool = False) -> tuple[str, list[str], str]:
    """(program, arguments, working folder) that start the Command Centre."""
    flags = ["--minimized"] if minimized else []
    if is_frozen():
        exe = Path(sys.executable)
        return str(exe), flags, str(exe.parent)

    root = Path(__file__).resolve().parents[3]
    # pythonw.exe: no console window.
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    program = pythonw if pythonw.is_file() else python
    return str(program), [str(root / "command_centre.py"), *flags], str(root)


def command_line(program: str, arguments: list[str]) -> str:
    return subprocess.list2cmdline([program, *arguments])


def icon_path() -> Path:
    if is_frozen():
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = bundled / "aurora" / "interface" / "assets" / "aurora.ico"
        if candidate.is_file():
            return candidate
    return Path(__file__).resolve().parents[1] / "assets" / "aurora.ico"


# --- Launch on startup --------------------------------------------------------------------

def _winreg():
    import winreg

    return winreg


def startup_enabled(registry=None) -> bool:
    if not IS_WINDOWS and registry is None:
        return False
    winreg = registry or _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Couldn't read the startup setting")
        return False


def set_startup(enabled: bool, registry=None) -> None:
    """Adds or removes AURORA from your user's startup programs (starts minimised to the tray)."""
    if not IS_WINDOWS and registry is None:
        raise RuntimeError("Launch on startup is only available on Windows.")
    winreg = registry or _winreg()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            program, arguments, _ = launch_command(minimized=True)
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command_line(program, arguments))
            logger.info("Launch on startup enabled")
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass
            logger.info("Launch on startup disabled")


# --- Shortcuts ----------------------------------------------------------------------------

def desktop_folder() -> Path:
    """The user's real desktop (also when it's redirected, e.g. to OneDrive)."""
    if IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            CSIDL_DESKTOPDIRECTORY = 0x10
            if ctypes.windll.shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buffer) == 0:
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    return Path.home() / "Desktop"


def _set_shortcut_app_id(path: Path, app_id: str) -> bool:
    """Stores the app identity in the shortcut, so pinning groups with the running window."""
    try:
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shellcon
    except ImportError:
        logger.info("pywin32 isn't installed; the shortcut has no app identity")
        return False

    # Explorer often opens a brand-new desktop file for a moment; retry briefly.
    for attempt in range(5):
        try:
            store = propsys.SHGetPropertyStoreFromParsingName(
                str(path), None, shellcon.GPS_READWRITE, propsys.IID_IPropertyStore)
            store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(app_id, pythoncom.VT_LPWSTR))
            store.Commit()
            return True
        except Exception as error:
            last = error
            import time
            time.sleep(0.4)
    logger.warning("Couldn't store the app identity in the shortcut: %s", last)
    return False


def create_shortcut(folder: Path | None = None, runner=subprocess.run) -> Path:
    """Creates 'AURORA Command Centre.lnk' (on the desktop by default)."""
    if not IS_WINDOWS:
        raise RuntimeError("Shortcuts can only be created on Windows.")
    folder = Path(folder or desktop_folder())
    path = folder / SHORTCUT_NAME
    program, arguments, working = launch_command()

    def quoted(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"   # PowerShell single-quoted string

    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + quoted(str(path)) + ");"
        f"$s.TargetPath = {quoted(program)};"
        f"$s.Arguments = {quoted(subprocess.list2cmdline(arguments))};"
        f"$s.WorkingDirectory = {quoted(working)};"
        f"$s.IconLocation = {quoted(str(icon_path()) + ',0')};"
        "$s.Description = 'AURORA Command Centre';"
        "$s.Save()"
    )
    runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
           check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if path.exists():
        _set_shortcut_app_id(path, APP_ID)
    logger.info("Shortcut created")
    return path
