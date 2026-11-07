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
    # And sensor history goes to a throwaway database.
    monkeypatch.setattr(settings, "SENSOR_DATABASE_PATH", tmp_path / "sensors_test.db")


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


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture
def memory(tmp_path):
    """A MemoryManager backed by a throwaway database, never the real one."""
    return MemoryManager(MemoryStore(tmp_path / "test_memory.db"))
