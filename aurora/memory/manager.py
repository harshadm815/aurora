from aurora.knowledge.fact import Fact
from aurora.memory.store import MemoryStore


class MemoryManager:
    """
    High-level interface for AURORA's long-term memory.
    """

    def __init__(self, store: MemoryStore | None = None):
        self.store = store or MemoryStore()

    def remember(self, fact: Fact) -> int:
        """
        Stores a fact unless an identical fact already exists.
        """

        existing = self.store.find_exact(fact)

        if existing is not None:
            return existing.id

        return self.store.add(fact)

    def memories(self) -> list[Fact]:
        """
        Returns all memories.
        """
        return self.store.all()

    def search(self, query: str) -> list[Fact]:
        """
        Searches long-term memory.
        """
        return self.store.search(query)

    def edit(
        self,
        memory_id: int,
        new_value: str
    ) -> bool:
        """
        Edits an existing memory.
        """
        return self.store.update_value(
            memory_id,
            new_value
        )

    def forget(self, memory_id: int) -> bool:
        """
        Deletes an existing memory.
        """
        return self.store.delete(memory_id)
