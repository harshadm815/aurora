"""
Procedural memory: routines the user teaches AURORA.

    "When I say morning briefing, check the weather in Pune and list my reminders"
        -> routine "morning briefing" = "check the weather in Pune and list my reminders"
    "morning briefing"   -> AURORA runs those steps, exactly as if they had been typed

A routine is stored as the request text, not as frozen tool calls, so it
goes through routing, planning, permissions and workflows every time it
runs. Before a routine is saved, its steps are checked: actions that
need a confirmation (devices, deleting memories, research sessions) and
memory commands can't be part of one.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from aurora.config import settings
from aurora.memory.store import connect, ensure_schema

_NAME_CLEAN = re.compile(r"[^\w\s'-]+")


def normalize_name(name: str) -> str:
    return " ".join(_NAME_CLEAN.sub(" ", name or "").casefold().split())


@dataclass(frozen=True)
class Routine:
    name: str
    steps: str
    created: datetime
    last_run: datetime | None
    times_run: int


class RoutineStore:

    def __init__(self, database_path=None):
        self.database_path = database_path or settings.DATABASE_PATH
        ensure_schema(self.database_path)

    def _connect(self):
        return connect(self.database_path)

    @staticmethod
    def _row(row) -> Routine:
        return Routine(row[0], json.loads(row[1])["text"], datetime.fromisoformat(row[2]),
                       datetime.fromisoformat(row[3]) if row[3] else None, row[4])

    def save(self, name: str, steps: str) -> tuple[Routine, bool]:
        """Saves (or replaces) a routine. Returns (routine, replaced)."""
        name = normalize_name(name)
        if not name:
            raise ValueError("A routine needs a name.")
        replaced = self.get(name) is not None
        if not replaced and len(self.all()) >= settings.ROUTINE_MAX:
            raise ValueError(f"I can keep up to {settings.ROUTINE_MAX} routines; delete one first.")
        payload = json.dumps({"text": " ".join(steps.split())})
        now = datetime.now().isoformat()
        with self._connect() as connection:
            if replaced:
                connection.execute("UPDATE routines SET steps = ? WHERE name = ?", (payload, name))
            else:
                connection.execute("INSERT INTO routines (name, steps, created) VALUES (?, ?, ?)", (name, payload, now))
        return self.get(name), replaced

    def get(self, name: str) -> Routine | None:
        with self._connect() as connection:
            row = connection.execute("SELECT name, steps, created, last_run, times_run FROM routines WHERE name = ?",
                                     (normalize_name(name),)).fetchone()
        return self._row(row) if row else None

    def all(self) -> list[Routine]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, steps, created, last_run, times_run FROM routines ORDER BY name").fetchall()
        return [self._row(r) for r in rows]

    def delete(self, name: str) -> bool:
        with self._connect() as connection:
            return connection.execute("DELETE FROM routines WHERE name = ?", (normalize_name(name),)).rowcount > 0

    def mark_run(self, name: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE routines SET last_run = ?, times_run = times_run + 1 WHERE name = ?",
                               (datetime.now().isoformat(), normalize_name(name)))

    _RUN = re.compile(r"^(?:please\s+)?(?:run|start|do|play)\s+(?:my\s+|the\s+)?(?P<name>.+?)(?:\s+routine)?$", re.I)

    def match(self, message: str) -> Routine | None:
        """The routine a message asks to run: its exact name, or "run <name>"."""
        text = (message or "").strip().rstrip(".!")
        routine = self.get(text)
        if routine is None:
            run = self._RUN.match(text)
            if run:
                routine = self.get(run.group("name"))
        return routine
