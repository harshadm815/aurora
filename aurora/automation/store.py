"""
AURORA's automation database (``settings.AUTOMATION_DATABASE_PATH``, not in git).

One SQLite file with four tables:

- ``audit``      every action AURORA was asked to take or took, and what happened;
- ``reminders``  reminders, kept until they are shown, cancelled or missed;
- ``tasks``      scheduled tasks you approved;
- ``file_moves`` each file a tidy-up moved, so it can be put back (undo).

Each call opens its own connection, so the scheduler thread, the
Command Centre's worker threads and the engine can all use it.
"""

import sqlite3
from pathlib import Path

from aurora.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    action TEXT NOT NULL,          -- e.g. devices.on, files.organize, schedule.run
    outcome TEXT NOT NULL,         -- asked, approved, declined, cancelled, done, failed, undone, skipped
    summary TEXT NOT NULL,
    initiated TEXT NOT NULL,       -- "you" or "schedule #3"
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS audit_time ON audit (time);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due TEXT NOT NULL,
    created TEXT NOT NULL,
    status TEXT NOT NULL           -- pending, shown, cancelled, missed
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,            -- what it does, in words
    kind TEXT NOT NULL,            -- reminder, routine, device, sensors
    target TEXT NOT NULL,          -- JSON: what exactly to do
    days TEXT NOT NULL,            -- daily, weekdays, weekends, or e.g. "mon,thu"
    at TEXT NOT NULL,              -- HH:MM
    created TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    last_run TEXT,
    last_result TEXT,
    last_success INTEGER,
    failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS file_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch TEXT NOT NULL,           -- one tidy-up
    source TEXT NOT NULL,
    destination TEXT NOT NULL,
    created_folder TEXT,           -- a folder this move created (removed on undo if empty)
    time TEXT NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS file_moves_batch ON file_moves (batch);
"""


class AutomationStore:

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or settings.AUTOMATION_DATABASE_PATH)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection
