"""
Tools that let the user explicitly manage AURORA's long-term memory.

- Remember:      store something the user asked AURORA to keep (optionally
                 pinned, "always remember …", or for a while, "remember for
                 today …").
- Recall:        look up stored memories by keyword, or say how AURORA
                 knows something ("how do you know where I live?").
- Forget:        delete memories, but only after the user confirms.
- MemoryControl: take back what was just learned ("don't remember that"),
                 pause / resume learning, switch activity history on / off
                 and clear it.

Automatic learning from ordinary conversation lives in aurora/memory/auto.py.
"""

import re
from datetime import datetime, timedelta

from aurora.knowledge.fact import EXPLICIT, Fact
from aurora.kernel.permissions import Permission
from aurora.memory.auto import describe
from aurora.memory.evaluator import MemoryEvaluator
from aurora.memory.extractor import FactExtractor
from aurora.memory.manager import MemoryManager
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

NOTE_CATEGORY = "Note"
MAX_PROVENANCE = 3

_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}


def _bullets(facts: list[Fact]) -> str:
    return "\n".join(f"  - {describe(fact)}" for fact in facts)


def expiry_for(span: str, now: datetime | None = None) -> datetime | None:
    """'today' -> end of today, '3 days' -> 3 days from now, ... (None if not understood)."""
    now = now or datetime.now()
    span = " ".join((span or "").lower().split())
    end_of = lambda day: day.replace(hour=23, minute=59, second=59, microsecond=0)  # noqa: E731
    if span in ("today", "tonight"):
        return end_of(now)
    if span == "tomorrow":
        return end_of(now + timedelta(days=1))
    if span in ("this week", "the end of the week"):
        return end_of(now + timedelta(days=6 - now.weekday()))
    match = re.fullmatch(r"(\d+|a|an|one|two|three|four|five|six|seven)\s+(hour|day|week)s?", span)
    if match:
        amount = int(match.group(1)) if match.group(1).isdigit() else _NUMBER_WORDS[match.group(1)]
        unit = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(weeks=1)}[match.group(2)]
        return now + amount * unit
    return None


class RememberTool(BaseTool):

    required_permissions = (Permission.MEMORY_WRITE,)

    def __init__(self, memory: MemoryManager):
        super().__init__("Remember")
        self.memory = memory
        self.extractor = FactExtractor()
        self.evaluator = MemoryEvaluator()

    def execute(self, content: str = "", pin: bool = False, keep_for: str | None = None, **_) -> ToolResult:
        content = content.strip()
        if not content:
            return ToolResult(False, "What would you like me to remember?")
        expires = expiry_for(keep_for) if keep_for else None
        if keep_for and expires is None:
            return ToolResult(False, f"I didn't understand how long to keep it (\"{keep_for}\").")

        # "remember that my name is Harry" becomes a structured fact, so a
        # later "my name is Harshad" updates it instead of contradicting it.
        candidates = [f for f in self.extractor.extract(content, list(self.memory.people()))
                      if self.evaluator.evaluate(f).keep]
        if candidates:
            results = [self.memory.learn(fact, source=EXPLICIT, source_text=content) for fact in candidates]
            results = [r for r in results if r.action != "blocked"] or results
            for result in results:
                if result.fact.id is not None:
                    self._retain(result.fact.id, pin, expires)
            lines = []
            for result in results:
                if result.action == "updated":
                    lines.append(f"{describe(result.fact)} (was {describe(result.previous)})")
                elif result.action == "blocked":
                    return ToolResult(False, f"You asked me not to remember {describe(result.fact)}. "
                                             "You can allow it again in the Memory Centre.", results)
                else:
                    lines.append(describe(result.fact))
            note = self._note(pin, expires)
            if all(r.action == "duplicate" for r in results):
                return ToolResult(True, "I already have that noted: " + "; ".join(lines) + note, results)
            return ToolResult(True, "Noted. I'll remember " + "; ".join(lines) + note, results)

        fact = Fact(category=NOTE_CATEGORY, entity="User", attribute="Note", value=content, priority=3)
        result = self.memory.learn(fact, source=EXPLICIT, source_text=content)
        if result.action == "blocked":
            return ToolResult(False, "You asked me not to remember that. You can allow it again in the Memory Centre.")
        self._retain(result.fact.id, pin, expires)
        note = self._note(pin, expires)
        if result.action == "duplicate":
            return ToolResult(True, f"I already have that noted: \"{content}\"{note}", result.fact)
        return ToolResult(True, f"Noted. I'll remember: \"{content}\"{note}", result.fact)

    def _retain(self, memory_id: int, pin: bool, expires: datetime | None) -> None:
        if pin:
            self.memory.pin(memory_id, True)
        elif expires is not None:
            self.memory.set_expiry(memory_id, expires)

    @staticmethod
    def _note(pin: bool, expires: datetime | None) -> str:
        if pin:
            return " (pinned: I'll always keep it)"
        if expires is not None:
            return f" (until {expires:%a %d %b %H:%M}, then I'll forget it)"
        return ""


class RecallTool(BaseTool):

    required_permissions = (Permission.MEMORY_READ,)

    def __init__(self, memory: MemoryManager):
        super().__init__("Recall")
        self.memory = memory

    def execute(self, query: str = "", mode: str = "recall", **_) -> ToolResult:
        result = self.memory.recall(query)
        self.memory.mark_used(result.matches)

        if mode == "provenance":
            facts = result.matches or result.related
            if not facts or not result.keywords:
                return ToolResult(True, f"I don't have anything saved about {query}.", result)
            lines = [f"{describe(f)}: {self.memory.provenance(f)}" for f in facts[:MAX_PROVENANCE]]
            return ToolResult(True, "\n".join(lines), result)

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
                fact.entity.lower() == m.entity.lower()
                and fact.attribute.lower() == m.attribute.lower()
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


class MemoryControlTool(BaseTool):
    """The user's switches over what AURORA keeps. Needs the engine's
    AutoMemory (to know what was just learned) and episode log, which the
    engine provides through attach_memory()."""

    required_permissions = (Permission.MEMORY_READ, Permission.MEMORY_WRITE)

    def __init__(self, memory: MemoryManager):
        super().__init__("MemoryControl")
        self.memory = memory
        self.auto_memory = None
        self.episodes = None

    def attach_memory(self, engine) -> None:
        self.auto_memory = engine.auto_memory
        self.episodes = engine.episodes

    def execute(self, action: str = "", **_) -> ToolResult:
        if action == "undo":
            return self._undo()
        if action == "pause":
            self.memory.set_learning_paused(True)
            return ToolResult(True, "Okay, I've stopped learning from our conversations. I'll still remember "
                                    "things you explicitly ask me to. Say \"start learning again\" to undo this.")
        if action == "resume":
            self.memory.set_learning_paused(False)
            return ToolResult(True, "Okay, I'll learn from our conversations again (and say when I do).")
        if self.episodes is None:
            return ToolResult(False, "Activity history isn't available.")
        if action == "episodes_off":
            self.episodes.set_enabled(False)
            return ToolResult(True, "Okay, I've stopped recording our activity. What's already recorded stays "
                                    "until it expires; say \"clear my activity history\" to delete it now.")
        if action == "episodes_on":
            self.episodes.set_enabled(True)
            return ToolResult(True, "Okay, I'll keep a short history of what we do again "
                                    "(one line per task, kept for 30 days).")
        if action == "clear_episodes":
            count = self.episodes.count()
            if count == 0:
                return ToolResult(True, "There's no activity history to clear.")

            def clear() -> ToolResult:
                removed = self.episodes.clear()
                return ToolResult(True, f"Done. I deleted {removed} activity record{'s' if removed != 1 else ''}.")

            return ToolResult(True, f"I'll delete all {count} activity record{'s' if count != 1 else ''}. "
                                    "Should I go ahead? (yes / no)", count, confirm=clear)
        return ToolResult(False, "I'm not sure what you'd like me to change about my memory.")

    def _undo(self) -> ToolResult:
        recent = list(self.auto_memory.last_results) if self.auto_memory is not None else []
        if not recent:
            return ToolResult(False, "I didn't just learn anything, so there's nothing to take back. "
                                     "To delete something older, say \"forget …\".")
        undone = []
        for result in recent:
            if result.action == "added" and result.fact.id is not None:
                self.memory.forget(result.fact.id)
            elif result.action == "updated" and result.previous is not None and result.fact.id is not None:
                if result.previous.id == result.fact.id:
                    self.memory.store.update_value(result.fact.id, result.previous.value, reason="taken back by you")
                else:   # likes <-> dislikes: the old fact was replaced by a new row
                    self.memory.forget(result.fact.id)
                    self.memory.remember(result.previous)
            self.memory.block(result.fact)
            undone.append(describe(result.fact))
        self.auto_memory.last_results = []
        return ToolResult(True, f"Okay, I've forgotten {'; '.join(undone)}, and I won't learn "
                                f"{'it' if len(undone) == 1 else 'them'} again. "
                                "(You can allow it again in the Memory Centre.)", undone)
