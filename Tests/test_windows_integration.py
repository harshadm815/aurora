"""
Windows integration tests. The registry and PowerShell are faked: tests
never change your real startup programs or desktop.
"""

import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from aurora.interface.windows import integration
from aurora.interface.windows.icon import ICON_PATH, SIZES, build_ico, render_png


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# --- Icon -------------------------------------------------------------------------------

def test_committed_icon_has_every_size():
    data = ICON_PATH.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(SIZES))

    sizes = []
    for index in range(count):
        width, height, *_ , size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * index: 22 + 16 * index])
        sizes.append(width or 256)
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n"     # PNG-compressed entry
        assert offset + size <= len(data)
    assert sorted(sizes) == sorted(SIZES)


def test_icon_renders(qapp):
    png = render_png(48)

    assert png.startswith(b"\x89PNG")
    assert build_ico({48: png})[:6] == struct.pack("<HHH", 0, 1, 1)


# --- Launch command ---------------------------------------------------------------------

def test_launch_command_from_source():
    program, arguments, working = integration.launch_command(minimized=True)

    assert program.lower().endswith(("pythonw.exe", "python.exe", "python", "pythonw"))
    assert arguments[0].endswith("command_centre.py")
    assert arguments[-1] == "--minimized"


def test_launch_command_when_packaged(monkeypatch, tmp_path):
    exe = tmp_path / "AURORA Command Centre.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    program, arguments, working = integration.launch_command(minimized=True)

    assert program == str(exe)
    assert arguments == ["--minimized"]
    assert working == str(tmp_path)


# --- Launch on startup (fake registry) --------------------------------------------------

class FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    class _Key:
        def __init__(self, registry):
            self.registry = registry

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved, access):
        assert root == "HKCU" and path == integration.RUN_KEY   # Only the user's Run key.
        return self._Key(self)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_startup_on_and_off():
    registry = FakeRegistry()
    assert integration.startup_enabled(registry) is False

    integration.set_startup(True, registry)

    assert integration.startup_enabled(registry) is True
    command = registry.values[integration.RUN_VALUE]
    assert "command_centre.py" in command and command.endswith("--minimized")

    integration.set_startup(False, registry)
    assert integration.startup_enabled(registry) is False
    integration.set_startup(False, registry)          # Already off: no error.


# --- Desktop shortcut (fake PowerShell) ---------------------------------------------------

@pytest.mark.skipif(not integration.IS_WINDOWS, reason="Windows shortcuts")
def test_shortcut_command(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)

    path = integration.create_shortcut(tmp_path, runner=runner)

    assert path == tmp_path / integration.SHORTCUT_NAME
    script = calls[0][-1]
    assert calls[0][:2] == ["powershell", "-NoProfile"]
    assert "CreateShortcut('" + str(path) + "')" in script
    assert "aurora.ico,0" in script
    assert "command_centre.py" in script


@pytest.mark.skipif(not integration.IS_WINDOWS, reason="Windows shortcuts")
def test_shortcut_quotes_awkward_paths(tmp_path):
    folder = tmp_path / "Harry's Desktop"
    folder.mkdir()
    calls = []

    integration.create_shortcut(folder, runner=lambda command, **kwargs: calls.append(command))

    assert "Harry''s Desktop" in calls[0][-1]       # PowerShell-escaped, not breaking the string.


# --- Single instance ----------------------------------------------------------------------

def test_second_instance_hands_over_to_the_first(qapp):
    from aurora.interface.backend import wait_for
    from aurora.interface.windows.single_instance import SingleInstance

    name = f"AURORA-test-{os.getpid()}"
    first = SingleInstance(name)
    shown = []
    first.activated.connect(lambda: shown.append(True))

    assert first.acquire() is True
    assert SingleInstance(name).acquire() is False      # Doesn't start a second AURORA.
    assert wait_for(lambda: shown, 3)                   # The first one was asked to show itself.

    first.release()
    third = SingleInstance(name)
    assert third.acquire() is True                      # After exit, AURORA can start again.
    third.release()


# --- Packaged data folder -----------------------------------------------------------------

def test_packaged_app_uses_the_data_folder(monkeypatch, tmp_path):
    from aurora.config import settings

    exe_folder = tmp_path / "dist"
    exe_folder.mkdir()
    data = tmp_path / "AuroraData"
    data.mkdir()
    (exe_folder / "aurora_home.txt").write_text(str(data), encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_folder / "AURORA Command Centre.exe"))

    assert settings._project_root() == data.resolve()

    (exe_folder / "aurora_home.txt").unlink()
    assert settings._project_root() == exe_folder.resolve()


# --- Backend: pause, resume, shutdown -----------------------------------------------------

@pytest.fixture
def backend(qapp, memory, tmp_path, monkeypatch):
    from aurora.brain.brain import Brain
    from aurora.config import preferences
    from aurora.interface.backend import Backend, wait_for
    from aurora.kernel.engine import AuroraEngine

    from conftest import FakeProvider

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")

    class UnloadingProvider(FakeProvider):
        unloaded = 0

        def unload(self):
            UnloadingProvider.unloaded += 1

    provider = UnloadingProvider()
    b = Backend(engine_factory=lambda: AuroraEngine(Brain(provider), memory=memory))
    b.provider = provider
    b.start()
    assert wait_for(lambda: b.state == "running", 10)
    yield b
    b.shutdown()


def test_pause_stops_services_and_unloads_the_model(backend):
    from aurora.interface.backend import wait_for

    backend.pause()

    assert backend.state == "paused"
    assert wait_for(lambda: not backend.engine.running, 5)
    assert wait_for(lambda: backend.provider.unloaded == 1, 5)
    assert backend.engine.service_manager.status() == {"Brain": "Stopped"}

    errors = []
    backend.send_message("hello", pytest.fail, errors.append)
    assert "paused" in str(errors[0])


def test_resume_starts_everything_again(backend):
    from aurora.interface.backend import wait_for

    backend.pause()
    backend.resume()

    assert wait_for(lambda: backend.state == "running", 5)
    assert backend.engine.running
    replies = []
    backend.send_message("what is 2+2", replies.append, pytest.fail)
    assert wait_for(lambda: replies, 5)
    assert replies[0][0] == "2+2 = 4"


def test_shutdown_stops_everything_and_is_safe_twice(backend):
    reminders = backend.engine.tool_manager.get("Reminders")
    from datetime import datetime, timedelta
    reminders.scheduler.add("test", datetime.now() + timedelta(hours=1))

    backend.shutdown()
    backend.shutdown()

    assert backend.state == "stopped"
    assert not backend.engine.running
    assert reminders.scheduler.pending() == []
    assert backend._engine_pool.activeThreadCount() == 0
    assert backend._io_pool.activeThreadCount() == 0


# --- Tray and window ----------------------------------------------------------------------

def test_tray_menu_follows_state(backend):
    from PySide6.QtGui import QIcon

    from aurora.interface.windows.tray import Tray

    tray = Tray(backend, QIcon(str(ICON_PATH)))
    labels = [a.text() for a in tray.menu.actions() if a.text()]

    assert labels == ["Open AURORA", "Start AURORA", "Pause AURORA", "Settings", "Exit"]
    assert tray.pause_action.isEnabled() and not tray.start_action.isEnabled()

    tray.pause_action.trigger()
    assert tray.start_action.isEnabled() and not tray.pause_action.isEnabled()
    assert tray.icon.toolTip() == "AURORA: paused (model unloaded)"


def test_window_goes_to_tray_and_exit_really_quits(backend, monkeypatch):
    from PySide6.QtGui import QIcon

    from aurora.interface.main_window import MainWindow
    from aurora.interface.windows.tray import Tray

    monkeypatch.setattr(Tray, "available", staticmethod(lambda: True))
    window = MainWindow(backend)
    window.show()
    backend.preferences["close_to_tray"] = True

    window.close()                                   # Close button: hides to the tray.
    assert not window.isVisible()
    assert backend.state == "running"

    window.show_from_tray()
    assert window.isVisible()

    monkeypatch.setattr(QApplication, "quit", lambda *a: None)
    window.tray.exit_action.trigger()                # Exit: really shuts down.
    assert backend.state == "stopped"
