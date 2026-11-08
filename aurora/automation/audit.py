"""
The audit trail: a permanent record of every action AURORA was asked to
take, and what happened.

    asked      AURORA asked you to confirm ("Shall I turn on the desk light?")
    declined   you said no
    cancelled  you moved on without answering, or stopped it part way
    done       it happened
    failed     it was tried and didn't work (the reason is in the summary)
    undone     it was reversed
    skipped    a scheduled run that didn't happen (AURORA was closed, or the task is paused)

Each entry says who started it ("you" or "schedule #3") and whether the
device was simulated. Entries are kept for ``AUDIT_RETENTION_DAYS`` (90)
in the automation database; AURORA's log file only ever gets the action
name and outcome, never the summary.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from aurora.automation.store import AutomationStore
from aurora.config import settings

logger = logging.getLogger(__name__)

OUTCOMES = ("asked", "declined", "cancelled", "done", "failed", "undone", "skipped")


@dataclass(frozen=True)
class AuditEntry:
    id: int
    time: datetime
    action: str
    outcome: str
    summary: str
    initiated: str
    simulated: bool

    def line(self) -> str:
        sim = " (simulated)" if self.simulated else ""
        by = "" if self.initiated == "you" else f" [{self.initiated}]"
        return f"{self.time:%d %b %H:%M}  {self.outcome:<9} {self.action}: {self.summary}{sim}{by}"


class AuditLog:

    def __init__(self, store: AutomationStore | None = None):
        self._store = store

    @property
    def store(self) -> AutomationStore:
        if self._store is None:
            self._store = AutomationStore()
        return self._store

    def record(self, action: str, outcome: str, summary: str, initiated: str = "you",
               simulated: bool = False) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {outcome!r}")
        summary = " ".join(str(summary).split())[:300]
        now = datetime.now()
        try:
            with self.store.connect() as connection:
                connection.execute(
                    "INSERT INTO audit (time, action, outcome, summary, initiated, simulated) VALUES (?, ?, ?, ?, ?, ?)",
                    (now.isoformat(timespec="seconds"), action, outcome, summary, initiated, int(simulated)))
                connection.execute("DELETE FROM audit WHERE time < ?",
                                   ((now - timedelta(days=settings.AUDIT_RETENTION_DAYS)).isoformat(),))
        except Exception:
            # The audit trail must never stop an answer, but its failure must be visible.
            logger.exception("Writing the audit trail failed (%s %s)", action, outcome)
            return
        logger.info("Audit: %s %s%s", action, outcome, "" if initiated == "you" else f" ({initiated})")

    def recent(self, limit: int = 100, action: str | None = None) -> list[AuditEntry]:
        query = "SELECT * FROM audit"
        parameters: tuple = ()
        if action:
            query += " WHERE action = ? OR action LIKE ?"
            parameters = (action, f"{action}.%")
        query += " ORDER BY id DESC LIMIT ?"
        with self.store.connect() as connection:
            rows = connection.execute(query, (*parameters, limit)).fetchall()
        return [AuditEntry(row["id"], datetime.fromisoformat(row["time"]), row["action"], row["outcome"],
                           row["summary"], row["initiated"], bool(row["simulated"])) for row in rows]
