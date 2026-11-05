"""
Tools that let the user explicitly manage AURORA's long-term memory.

- Remember: store a note the user asked AURORA to keep.
- Recall:   look up stored memories by keyword.
- Forget:   delete memories, but only after the user confirms.

Automatic learning from ordinary conversation lives in aurora/memory/auto.py.
"""

from aurora.knowledge.fact import Fact
from aurora.kernel.permissions import Permission
from aurora.memory.auto import describe
from aurora.memory.evaluator import MemoryEvaluator
from aurora.memory.extractor import FactExtractor
from aurora.memory.manager import MemoryManager
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

NOTE_CATEGORY = "Note"


def _bullets(facts: list[Fact]) -> str:
    return "\n".join(f"  - {describe(fact)}" for fact in facts)


class RememberTool(BaseTool):

    required_permissions = (Permission.MEMORY_WRITE,)

    def __init__(self, memory: MemoryManager):
        super().__init__("Remember")
        self.memory = memory
        self.extractor = FactExtractor()
        self.evaluator = MemoryEvaluator()

    def execute(self, content: str = "", **_) -> ToolResult:
        content = content.strip()
        if not content:
            return ToolResult(False, "What would you like me to remember?")

        # "remember that my name is Harry" becomes a structured fact, so a
        # later "my name is Harshad" updates it instead of contradicting it.
        candidates = [f for f in self.extractor.extract(content) if self.evaluator.evaluate(f).keep]
        if candidates:
            results = [self.memory.learn(fact) for fact in candidates]
            lines = []
            for result in results:
                if result.action == "updated":
                    lines.append(f"{describe(result.fact)} (was {describe(result.previous)})")
                else:
                    lines.append(describe(result.fact))
            if all(r.action == "duplicate" for r in results):
                return ToolResult(True, "I already have that noted: " + "; ".join(lines), results)
            return ToolResult(True, "Noted. I'll remember " + "; ".join(lines), results)

        fact = Fact(
            category=NOTE_CATEGORY,
            entity="User",
            attribute="Note",
            value=content,
            priority=3,
        )

        if self.memory.contains(fact):
            return ToolResult(True, f"I already have that noted: \"{content}\"", fact)

        fact.id = self.memory.remember(fact)
        return ToolResult(True, f"Noted. I'll remember: \"{content}\"", fact)


class RecallTool(BaseTool):

    required_permissions = (Permission.MEMORY_READ,)

    def __init__(self, memory: MemoryManager):
        super().__init__("Recall")
        self.memory = memory

    def execute(self, query: str = "", **_) -> ToolResult:
        result = self.memory.recall(query)
        self.memory.mark_used(result.matches)

        if result.matches:
            heading = (
                "Here's what I have saved:" if not result.keywords
                else "Here's what I remember:"
            )
            return ToolResult(True, f"{heading}\n{_bullets(result.matches)}", result)

        if not result.keywords:
            return ToolResult(True, "I don't have anything saved yet.", result)

        if result.related:
            return ToolResult(
                True,
                f"I don't have anything saved about {query}. "
                f"Related things I do have:\n{_bullets(result.related)}",
                result,
            )

        return ToolResult(True, f"I don't have anything saved about {query}.", result)


class ForgetTool(BaseTool):

    required_permissions = (Permission.MEMORY_READ, Permission.MEMORY_WRITE)

    def __init__(self, memory: MemoryManager):
        super().__init__("Forget")
        self.memory = memory

    def execute(self, query: str = "", **_) -> ToolResult:
        result = self.memory.recall(query, limit=50)

        if not result.matches:
            return ToolResult(False, f"I don't have anything saved about {query}.", result)

        # Include exact duplicates so they're removed too.
        targets = [
            fact for fact in self.memory.memories()
            if any(
                fact.attribute.lower() == m.attribute.lower()
                and fact.value.lower() == m.value.lower()
                for m in result.matches
            )
        ]

        def delete() -> ToolResult:
            deleted = sum(1 for fact in targets if self.memory.forget(fact.id))
            noun = "memory" if deleted == 1 else "memories"
            return ToolResult(True, f"Done. I deleted {deleted} {noun}.", deleted)

        count = len(result.matches)
        noun = "this memory" if count == 1 else f"these {count} memories"
        return ToolResult(
            True,
            f"I'll delete {noun}:\n{_bullets(result.matches)}\n"
            "Should I go ahead? (yes / no)",
            result,
            confirm=delete,
        )
