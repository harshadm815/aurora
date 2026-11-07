"""
SQLite storage for AURORA's long-term memory (aurora_memory.db).

Tables:
    facts         semantic memory: one row per fact, with provenance and
                  retention fields
    fact_history  every change to a fact's value: old, new, when, why
    blocked       facts the user said not to remember; never learned again
    episodes      episodic memory: one short summary per completed task
    routines      procedural memory: named routines the user taught
    memory_settings  the user's memory switches (learning paused, ...)

Schema versions (PRAGMA user_version):
    0  the original facts table (AURORA up to 0.25)
    1  provenance and retention columns + the tables above (0.26)

Upgrading never rewrites or removes data: columns and tables are only
added, and the database file is copied to
"aurora_memory.db.before-schema-1.bak" first.
"""

import logging
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from aurora.config import settings
from aurora.knowledge.fact import UNKNOWN, Fact

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

FACT_COLUMNS = (
    "id, category, entity, attribute, value, priority, confidence, created, last_used, times_used, "
    "source, source_text, updated, pinned, expires, times_confirmed"
)

_NEW_FACT_COLUMNS = [
    ("source", f"TEXT NOT NULL DEFAULT '{UNKNOWN}'"),
    ("source_text", "TEXT"),
    ("updated", "TEXT"),
    ("pinned", "INTEGER NOT NULL DEFAULT 0"),
    ("expires", "TEXT"),
    ("times_confirmed", "INTEGER NOT NULL DEFAULT 1"),
]

_TABLES = """
CREATE TABLE IF NOT EXISTS fact_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id INTEGER NOT NULL,
    changed TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blocked (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    attribute TEXT NOT NULL,
    value TEXT NOT NULL,
    value_key TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    steps TEXT NOT NULL,
    created TEXT NOT NULL,
    last_run TEXT,
    times_run INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memory_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@contextmanager
def connect(database_path):
    """A connection that commits on success, rolls back on error, and is
    always closed (sqlite3's own context manager doesn't close)."""
    connection = sqlite3.connect(database_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def ensure_schema(database_path) -> None:
    """Create or upgrade the memory database to SCHEMA_VERSION."""
    path = Path(database_path)
    existed = path.is_file() and path.stat().st_size > 0
    with connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        has_facts = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'facts'").fetchone() is not None
    if version >= SCHEMA_VERSION and has_facts:
        return

    if existed and has_facts and version < SCHEMA_VERSION:
        backup = path.with_name(f"{path.name}.before-schema-{SCHEMA_VERSION}.bak")
        if not backup.exists():
            shutil.copy2(path, backup)
            logger.info("Memory database backed up before upgrading to schema %d", SCHEMA_VERSION)

    with connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                entity TEXT NOT NULL,
                attribute TEXT NOT NULL,
                value TEXT NOT NULL,
                priority INTEGER NOT NULL,
                confidence REAL NOT NULL,
                created TEXT NOT NULL,
                last_used TEXT,
                times_used INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        present = {row[1] for row in connection.execute("PRAGMA table_info(facts)")}
        for column, definition in _NEW_FACT_COLUMNS:
            if column not in present:
                connection.execute(f"ALTER TABLE facts ADD COLUMN {column} {definition}")
        connection.executescript(_TABLES)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    logger.info("Memory database at schema %d", SCHEMA_VERSION)


class MemoryStore:
    """
    SQLite storage layer for semantic memory (facts), their history and
    the block list. Episodes and routines have their own small stores in
    the same database file.
    """

    def __init__(self, database_path: str | Path | None = None):
        # Resolved at call time (not import time) so tests can point
        # settings.DATABASE_PATH at a temporary file.
        self.database_path = database_path or settings.DATABASE_PATH
        ensure_schema(self.database_path)

    def _connect(self):
        return connect(self.database_path)

    @staticmethod
    def _row_to_fact(row) -> Fact:
        return Fact(
            id=row[0], category=row[1], entity=row[2], attribute=row[3], value=row[4],
            priority=row[5], confidence=row[6], created=_time(row[7]), last_used=_time(row[8]),
            times_used=row[9], source=row[10], source_text=row[11], updated=_time(row[12]),
            pinned=bool(row[13]), expires=_time(row[14]), times_confirmed=row[15],
        )

    def _select(self, where: str = "", params=(), order: str = "priority DESC, id ASC") -> list[Fact]:
        sql = f"SELECT {FACT_COLUMNS} FROM facts" + (f" WHERE {where}" if where else "") + f" ORDER BY {order}"
        with self._connect() as connection:
            return [self._row_to_fact(row) for row in connection.execute(sql, params).fetchall()]

    # --------------------------------------------------------------- facts

    def add(self, fact: Fact) -> int:
        """Adds a new fact; returns its id."""
        created = fact.created or datetime.now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO facts (category, entity, attribute, value, priority, confidence, created,
                                   last_used, times_used, source, source_text, updated, pinned, expires,
                                   times_confirmed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fact.category, fact.entity, fact.attribute, fact.value, fact.priority, fact.confidence,
                 created.isoformat(), _iso(fact.last_used), fact.times_used, fact.source, fact.source_text,
                 _iso(fact.updated), int(fact.pinned), _iso(fact.expires), fact.times_confirmed),
            )
            return cursor.lastrowid

    def get(self, memory_id: int) -> Fact | None:
        facts = self._select("id = ?", (memory_id,))
        return facts[0] if facts else None

    def all(self) -> list[Fact]:
        return self._select()

    def find_by_attribute(self, entity: str, attribute: str) -> list[Fact]:
        """Every fact about ``entity`` with this attribute (case-insensitive), oldest first."""
        return self._select("LOWER(entity) = LOWER(?) AND LOWER(attribute) = LOWER(?)",
                            (entity, attribute), order="id ASC")

    def find_exact(self, fact: Fact) -> Fact | None:
        """An existing fact with the same category, entity, attribute and value
        (compared as written, ignoring case)."""
        facts = self._select(
            "LOWER(category) = LOWER(?) AND LOWER(entity) = LOWER(?) AND LOWER(attribute) = LOWER(?) "
            "AND LOWER(value) = LOWER(?)",
            (fact.category, fact.entity, fact.attribute, fact.value), order="id ASC")
        return facts[0] if facts else None

    def search(self, query: str) -> list[Fact]:
        """Facts whose category, entity, attribute or value contains the query."""
        pattern = f"%{query}%"
        return self._select(
            "category LIKE ? COLLATE NOCASE OR entity LIKE ? COLLATE NOCASE "
            "OR attribute LIKE ? COLLATE NOCASE OR value LIKE ? COLLATE NOCASE",
            (pattern,) * 4)

    def update_value(self, memory_id: int, new_value: str, reason: str = "edited",
                     source: str | None = None, source_text: str | None = None,
                     confidence: float | None = None) -> bool:
        """Changes a fact's value and records the change in fact_history."""
        now = datetime.now().isoformat()
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM facts WHERE id = ?", (memory_id,)).fetchone()
            if row is None:
                return False
            sets, params = ["value = ?", "updated = ?"], [new_value, now]
            if source is not None:
                sets.append("source = ?")
                params.append(source)
            if source_text is not None or source is not None:
                sets.append("source_text = ?")
                params.append(source_text)
            if confidence is not None:
                sets.append("confidence = ?")
                params.append(confidence)
            connection.execute(f"UPDATE facts SET {', '.join(sets)} WHERE id = ?", (*params, memory_id))
            connection.execute(
                "INSERT INTO fact_history (fact_id, changed, old_value, new_value, reason) VALUES (?, ?, ?, ?, ?)",
                (memory_id, now, row[0], new_value, reason))
            return True

    def confirm(self, memory_id: int, confidence: float, source: str | None = None,
                source_text: str | None = None) -> None:
        """The user said this again: count it and raise the confidence."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE facts SET times_confirmed = times_confirmed + 1, confidence = ? WHERE id = ?",
                (confidence, memory_id))
            if source is not None:
                connection.execute("UPDATE facts SET source = ?, source_text = COALESCE(?, source_text) WHERE id = ?",
                                   (source, source_text, memory_id))

    def set_pinned(self, memory_id: int, pinned: bool) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE facts SET pinned = ?, expires = CASE WHEN ? THEN NULL ELSE expires END "
                                        "WHERE id = ?", (int(pinned), int(pinned), memory_id))
            return cursor.rowcount > 0

    def set_expires(self, memory_id: int, expires: datetime | None) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE facts SET expires = ?, pinned = CASE WHEN ? IS NULL THEN pinned ELSE 0 END "
                                        "WHERE id = ?", (_iso(expires), _iso(expires), memory_id))
            return cursor.rowcount > 0

    def delete(self, memory_id: int) -> bool:
        """Deletes a fact and its history."""
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM facts WHERE id = ?", (memory_id,))
            connection.execute("DELETE FROM fact_history WHERE fact_id = ?", (memory_id,))
            return cursor.rowcount > 0

    def delete_expired(self, now: datetime | None = None) -> int:
        now = (now or datetime.now()).isoformat()
        with self._connect() as connection:
            ids = [r[0] for r in connection.execute(
                "SELECT id FROM facts WHERE expires IS NOT NULL AND expires <= ? AND pinned = 0", (now,))]
            for memory_id in ids:
                connection.execute("DELETE FROM facts WHERE id = ?", (memory_id,))
                connection.execute("DELETE FROM fact_history WHERE fact_id = ?", (memory_id,))
            return len(ids)

    def history(self, memory_id: int) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT changed, old_value, new_value, reason FROM fact_history WHERE fact_id = ? ORDER BY id",
                (memory_id,)).fetchall()
        return [{"changed": _time(r[0]), "old": r[1], "new": r[2], "reason": r[3]} for r in rows]

    def mark_used(self, memory_ids: list[int]) -> None:
        """Records that these memories were used (recalled or given to the Brain)."""
        if not memory_ids:
            return
        now = datetime.now().isoformat()
        with self._connect() as connection:
            connection.executemany(
                "UPDATE facts SET last_used = ?, times_used = times_used + 1 WHERE id = ?",
                [(now, memory_id) for memory_id in memory_ids])

    # ------------------------------------------------------------ block list

    def block(self, entity: str, attribute: str, value: str, value_key: str) -> None:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM blocked WHERE LOWER(entity) = LOWER(?) AND LOWER(attribute) = LOWER(?) AND value_key = ?",
                (entity, attribute, value_key)).fetchone()
            if not exists:
                connection.execute(
                    "INSERT INTO blocked (entity, attribute, value, value_key, created) VALUES (?, ?, ?, ?, ?)",
                    (entity, attribute, value, value_key, datetime.now().isoformat()))

    def is_blocked(self, entity: str, attribute: str, value_key: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM blocked WHERE LOWER(entity) = LOWER(?) AND LOWER(attribute) = LOWER(?) AND value_key = ?",
                (entity, attribute, value_key)).fetchone() is not None

    def blocked(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT id, entity, attribute, value, created FROM blocked ORDER BY id").fetchall()
        return [{"id": r[0], "entity": r[1], "attribute": r[2], "value": r[3], "created": _time(r[4])} for r in rows]

    def unblock(self, block_id: int) -> bool:
        with self._connect() as connection:
            return connection.execute("DELETE FROM blocked WHERE id = ?", (block_id,)).rowcount > 0

    # -------------------------------------------------------------- switches

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM memory_settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO memory_settings (key, value) VALUES (?, ?) "
                               "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
