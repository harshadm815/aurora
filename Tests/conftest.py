import pytest

from aurora.memory.manager import MemoryManager
from aurora.memory.store import MemoryStore
from aurora.providers.base_provider import BaseProvider


class FakeProvider(BaseProvider):
    """Stands in for Ollama so tests never need a running model."""

    def __init__(self, reply="Test reply."):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(list(messages))
        return self.reply


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Every test gets its own database; the real aurora_memory.db is never touched."""
    from aurora.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", tmp_path / "aurora_test.db")
    # Likewise the real Documents folder is never read by tests.
    monkeypatch.setattr(settings, "DOCUMENTS_DIR", tmp_path / "Documents")
    # And the knowledge index is built in a throwaway file.
    monkeypatch.setattr(settings, "KNOWLEDGE_INDEX_PATH", tmp_path / "knowledge_test.db")
    # Model and training folders are never the real ones.
    monkeypatch.setattr(settings, "MODELS_DIR", tmp_path / "Models")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "Data")
    # And sensor history goes to a throwaway database.
    monkeypatch.setattr(settings, "SENSOR_DATABASE_PATH", tmp_path / "sensors_test.db")
    # Automation (audit trail, reminders, scheduled tasks, file-move records) too,
    # and no test may tidy a real folder.
    monkeypatch.setattr(settings, "AUTOMATION_DATABASE_PATH", tmp_path / "automation_test.db")
    monkeypatch.setattr(settings, "ORGANIZE_FOLDERS", None)
    # Saved news articles likewise.
    monkeypatch.setattr(settings, "NEWS_DATABASE_PATH", tmp_path / "news_test.db")
    # And browser history, bookmarks and the browser profile.
    monkeypatch.setattr(settings, "BROWSER_DATABASE_PATH", tmp_path / "browser_test.db")
    monkeypatch.setattr(settings, "BROWSER_PROFILE_DIR", tmp_path / "Data" / "browser")
    # The Documents page shows only the test's Documents folder (never your real folders).
    monkeypatch.setattr(settings, "EXPLORER_PLACES", [])
    monkeypatch.setattr(settings, "EXPLORER_DATABASE_PATH", tmp_path / "explorer_test.db")
    # Paired phones and their access log (and the phone server on a free port, never 8765).
    monkeypatch.setattr(settings, "MOBILE_DATABASE_PATH", tmp_path / "mobile_test.db")
    monkeypatch.setattr(settings, "MOBILE_PORT", 0)


@pytest.fixture(autouse=True)
def restore_preference_settings(monkeypatch):
    """Saving preferences (Settings page, voice controls) writes to the global
    settings; put them back after every test so no test sees another's values."""
    from aurora.config import preferences, settings

    for attribute, _, _ in preferences.BACKEND.values():
        monkeypatch.setattr(settings, attribute, getattr(settings, attribute))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests must never reach a real service (Wikipedia, arXiv, Open-Meteo, Ollama)."""
    import socket

    def blocked(*args, **kwargs):
        raise RuntimeError("Tests must not use the network; inject a fake client.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def no_camera_or_microphone(monkeypatch):
    """Tests must never open the real webcam or microphone; they use fakes."""
    from aurora.vision.camera import Camera, VisionUnavailable
    from aurora.voice.listener import MicrophoneSource, VoiceUnavailable

    def camera_blocked(self, *args, **kwargs):
        raise VisionUnavailable("Tests must not open the real camera; inject a fake.")

    def microphone_blocked(self, *args, **kwargs):
        raise VoiceUnavailable("Tests must not open the real microphone; inject a fake.")

    monkeypatch.setattr(Camera, "__init__", camera_blocked)
    monkeypatch.setattr(MicrophoneSource, "__init__", microphone_blocked)


@pytest.fixture(autouse=True)
def no_browser_engine(monkeypatch):
    """Tests must never start Chromium (Qt WebEngine): it would be slow and could
    go online. The Browser page shows its "couldn't start" message instead, and
    browser tests pass a fake factory."""
    try:
        from aurora.interface import browser_view
    except ImportError:              # PySide6 not installed: no interface tests either
        return

    def engine_blocked(network):
        raise RuntimeError("Tests must not start the browser engine; pass a fake factory.")

    monkeypatch.setattr(browser_view, "_qt_web_factory", engine_blocked)


def release_widgets():
    """Pages made in a test have no parent window. Delete them while Qt is fully
    alive; left to Python's shutdown, Qt sometimes crashes the test run on exit
    (an access violation after every test has passed)."""
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:
        return
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture
def memory(tmp_path):
    """A MemoryManager backed by a throwaway database, never the real one."""
    return MemoryManager(MemoryStore(tmp_path / "test_memory.db"))
