"""
Episodic memory: what AURORA did for the user, one short line per task.

    07 Nov 10:42  Checked the weather in Pune
    07 Nov 10:43  Calculated the average of 6 numbers
    07 Nov 11:05  Looked up Black holes on Wikipedia

Summaries are built from the routed request's *metadata* (which tool,
which place or topic), never from the text of a message, and only for
tool tasks: ordinary conversation, memory commands, health readings and
camera contents are never recorded. Episodes are deleted after
settings.EPISODE_RETENTION_DAYS (30) days, can be switched off ("stop
recording activity") and cleared ("clear my activity history").
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from aurora.config import settings
from aurora.memory.store import connect, ensure_schema
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent

logger = logging.getLogger(__name__)

ENABLED_KEY = "episodes_enabled"
MAX_SUMMARY = 120

NUMBER_NAMES = {
    "mean": "average", "median": "median", "mode": "mode", "sum": "sum", "count": "count",
    "min": "minimum", "max": "maximum", "range": "range", "stdev": "standard deviation",
}


@dataclass(frozen=True)
class Episode:
    id: int
    time: datetime
    kind: str
    summary: str
    success: bool

    def line(self) -> str:
        return f"{self.time:%d %b %H:%M}  {self.summary}" + ("" if self.success else " (didn't work)")


def _short(text) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= 40 else text[:39] + "…"


def summarize(decision: RouteDecision) -> str | None:
    """One line describing a completed tool task, or None if it isn't recorded."""
    intent, args = decision.intent, decision.arguments
    if intent is Intent.WEATHER:
        place = args.get("location")
        when = args.get("when")
        text = "Checked the weather" + (f" in {_short(place)}" if place else "")
        return text + (f" ({when})" if when and when != "now" else "")
    if intent is Intent.CALCULATOR:
        return "Did a calculation"
    if intent is Intent.NUMBERS:
        operations = args.get("operations") or (args.get("operation"),)
        names = [NUMBER_NAMES.get(op) for op in operations if NUMBER_NAMES.get(op)]
        if names:
            count = len(args.get("numbers") or ())
            return f"Calculated the {' and '.join(names)} of {count} numbers"
        return "Worked out a percentage"
    if intent is Intent.KNOWLEDGE:
        return f"Looked up {_short(args.get('topic', 'a topic'))} on Wikipedia"
    if intent is Intent.PAPERS:
        return f"Searched arXiv for papers on {_short(args.get('topic', 'a topic'))}"
    if intent is Intent.NEWS:
        return "Searched the news"          # the topic isn't kept: it may be sensitive
    if intent is Intent.DOCUMENTS:
        return "Worked with your documents"
    if intent is Intent.VISION:
        return "Looked through the camera"
    if intent is Intent.SENSORS:
        return "Checked the room sensors"
    if intent is Intent.REMINDER:
        return "Managed your reminders"
    if intent is Intent.DEVICES:
        return "Controlled a device"
    if intent is Intent.RESEARCH:
        return "Ran a research session"
    # Conversation, memory commands, health readings: not recorded.
    return None


class EpisodeLog:

    def __init__(self, database_path=None):
        self.database_path = database_path or settings.DATABASE_PATH
        ensure_schema(self.database_path)

    def _connect(self):
        return connect(self.database_path)

    # --------------------------------------------------------------- switch

    @property
    def enabled(self) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM memory_settings WHERE key = ?", (ENABLED_KEY,)).fetchone()
        if row is None:
            return bool(settings.EPISODES_ENABLED)
        return row[0] == "1"

    def set_enabled(self, enabled: bool) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO memory_settings (key, value) VALUES (?, ?) "
                               "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                               (ENABLED_KEY, "1" if enabled else "0"))

    # -------------------------------------------------------------- writing

    def record(self, kind: str, summary: str, success: bool = True, when: datetime | None = None) -> int | None:
        if not self.enabled or not summary:
            return None
        summary = " ".join(summary.split())[:MAX_SUMMARY]
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO episodes (time, kind, summary, success) VALUES (?, ?, ?, ?)",
                ((when or datetime.now()).isoformat(), kind, summary, int(success)))
            return cursor.lastrowid

    def record_decision(self, decision: RouteDecision, success: bool = True) -> int | None:
        summary = summarize(decision)
        return self.record(decision.intent.value, summary, success) if summary else None

    def clear(self) -> int:
        with self._connect() as connection:
            return connection.execute("DELETE FROM episodes").rowcount

    def cleanup(self, now: datetime | None = None, days: int | None = None) -> int:
        days = settings.EPISODE_RETENTION_DAYS if days is None else days
        cutoff = ((now or datetime.now()) - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            removed = connection.execute("DELETE FROM episodes WHERE time < ?", (cutoff,)).rowcount
        if removed:
            logger.info("Old episodes removed: %d", removed)
        return removed

    # -------------------------------------------------------------- reading

    def recent(self, since: datetime | None = None, until: datetime | None = None,
               contains: list[str] | None = None, limit: int = 30) -> list[Episode]:
        """Newest first."""
        where, params = [], []
        if since is not None:
            where.append("time >= ?")
            params.append(since.isoformat())
        if until is not None:
            where.append("time < ?")
            params.append(until.isoformat())
        for word in contains or []:
            where.append("(summary LIKE ? COLLATE NOCASE OR kind LIKE ? COLLATE NOCASE)")
            params += [f"%{word}%", f"%{word}%"]
        sql = "SELECT id, time, kind, summary, success FROM episodes"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY time DESC, id DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(sql, (*params, limit)).fetchall()
        return [Episode(r[0], datetime.fromisoformat(r[1]), r[2], r[3], bool(r[4])) for r in rows]

    def count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
