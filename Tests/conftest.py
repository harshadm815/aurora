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


@pytest.fixture
def fake_provider():
    return FakeProvider()


@pytest.fixture
def memory(tmp_path):
    """A MemoryManager backed by a throwaway database, never the real one."""
    return MemoryManager(MemoryStore(tmp_path / "test_memory.db"))
