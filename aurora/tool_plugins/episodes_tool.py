"""
Episodes tool: answers questions about what AURORA has done for the user.

    "what did we do today?"            -> today's activity
    "when did I last check the weather?" -> the most recent matching task
    "have I looked up black holes this week?"
"""

from datetime import datetime, timedelta

from aurora.config import settings
from aurora.kernel.permissions import Permission
from aurora.memory.manager import keywords
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

# Verbs in a question that say nothing about which task is meant.
_VERBS = {"check", "look", "up", "ask", "calculat", "calculate", "search", "use", "read", "find", "get", "do",
          "ran", "run", "see", "set"}

MAX_LINES = 15


def period_range(period: str | None, now: datetime | None = None) -> tuple[datetime | None, datetime | None, str]:
    """(since, until, words for the reply)."""
    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period in (None, ""):
        return None, None, f"in the last {settings.EPISODE_RETENTION_DAYS} days"
    if period in ("today", "so far", "so far today"):
        return today, None, "today"
    if period == "yesterday":
        return today - timedelta(days=1), today, "yesterday"
    if period == "this week":
        return today - timedelta(days=today.weekday()), None, "this week"
    if period == "last week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=7), "last week"
    return now - timedelta(days=7), None, "in the last 7 days"        # recently, lately


class EpisodesTool(BaseTool):

    required_permissions = (Permission.MEMORY_READ,)

    def __init__(self):
        super().__init__("Episodes")
        self.episodes = None

    def attach_memory(self, engine) -> None:
        self.episodes = engine.episodes

    def execute(self, period: str | None = None, what: str | None = None, latest: bool = False, **_) -> ToolResult:
        if self.episodes is None:
            return ToolResult(False, "Activity history isn't available.")
        since, until, when_words = period_range(period)
        words = [w for w in keywords(what or "") if w not in _VERBS] if what else []
        found = self.episodes.recent(since, until, contains=words, limit=1 if latest else MAX_LINES)
        off = "" if self.episodes.enabled else (
            "\n(I'm not recording activity at the moment; say \"start recording activity\" to turn it back on.)")

        if not found:
            about = f" about {what}" if what else ""
            return ToolResult(True, f"I don't have any record{about} {when_words}. "
                                    f"(I keep a short activity history for {settings.EPISODE_RETENTION_DAYS} days.){off}",
                              [])
        if latest:
            episode = found[0]
            return ToolResult(True, f"The last time was {episode.time:%a %d %b at %H:%M}: {episode.summary}"
                                    + ("" if episode.success else " (it didn't work)") + f".{off}", found)
        heading = f"Here's what we did {when_words}:" if not what else f"Here's what I have {when_words}:"
        lines = "\n".join(f"  - {episode.line()}" for episode in reversed(found))
        return ToolResult(True, f"{heading}\n{lines}{off}", found)
