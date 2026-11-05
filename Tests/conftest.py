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


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests must never reach a real service (Wikipedia, arXiv, Open-Meteo, Ollama)."""
    import socket

    def blocked(*args, **kwargs):
        raise RuntimeError("Tests must not use the network; inject a fake client.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture
def memory(tmp_path):
    """A MemoryManager backed by a throwaway database, never the real one."""
    return MemoryManager(MemoryStore(tmp_path / "test_memory.db"))
