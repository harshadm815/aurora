import sqlite3
from datetime import datetime
from pathlib import Path

from aurora.config import settings
from aurora.knowledge.fact import Fact


class MemoryStore:
    """
    SQLite storage layer for AURORA's long-term memory.

    Responsibilities:
    - Create the memory database.
    - Add facts.
    - Retrieve facts.
    - Search facts.
    - Detect exact duplicates.
    - Update facts.
    - Delete facts.
    """

    def __init__(self, database_path: str | Path | None = None):
        # Resolved at call time (not import time) so tests can point
        # settings.DATABASE_PATH at a temporary file.
        self.database_path = database_path or settings.DATABASE_PATH
        self._create_database()

    def _connect(self):
        """
        Creates and returns a connection to the SQLite database.
        """
        return sqlite3.connect(self.database_path)

    def _create_database(self):
        """
        Creates the facts table if it does not already exist.
        """
        with self._connect() as connection:
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

    def _row_to_fact(self, row) -> Fact:
        """
        Converts a SQLite database row into a Fact object.
        """
        return Fact(
            id=row[0],
            category=row[1],
            entity=row[2],
            attribute=row[3],
            value=row[4],
            priority=row[5],
            confidence=row[6],
            created=datetime.fromisoformat(row[7]),
            last_used=(
                datetime.fromisoformat(row[8])
                if row[8]
                else None
            ),
            times_used=row[9],
        )

    def add(self, fact: Fact) -> int:
        """
        Adds a new fact to the database.

        Returns the ID assigned to the new memory.
        """
        created = fact.created or datetime.now()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO facts (
                    category,
                    entity,
                    attribute,
                    value,
                    priority,
                    confidence,
                    created,
                    last_used,
                    times_used
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.category,
                    fact.entity,
                    fact.attribute,
                    fact.value,
                    fact.priority,
                    fact.confidence,
                    created.isoformat(),
                    (
                        fact.last_used.isoformat()
                        if fact.last_used
                        else None
                    ),
                    fact.times_used,
                ),
            )

            memory_id = cursor.lastrowid

        return memory_id

    def all(self) -> list[Fact]:
        """
        Returns all stored facts.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    category,
                    entity,
                    attribute,
                    value,
                    priority,
                    confidence,
                    created,
                    last_used,
                    times_used
                FROM facts
                ORDER BY priority DESC, id ASC
                """
            ).fetchall()

        return [
            self._row_to_fact(row)
            for row in rows
        ]

    def find_exact(self, fact: Fact) -> Fact | None:
        """
        Finds an exact duplicate of a fact.

        Returns the existing Fact if found.
        Otherwise returns None.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    category,
                    entity,
                    attribute,
                    value,
                    priority,
                    confidence,
                    created,
                    last_used,
                    times_used
                FROM facts
                WHERE LOWER(category) = LOWER(?)
                  AND LOWER(entity) = LOWER(?)
                  AND LOWER(attribute) = LOWER(?)
                  AND LOWER(value) = LOWER(?)
                LIMIT 1
                """,
                (
                    fact.category,
                    fact.entity,
                    fact.attribute,
                    fact.value,
                ),
            ).fetchone()

        if row is None:
            return None

        return self._row_to_fact(row)

    def search(self, query: str) -> list[Fact]:
        """
        Searches for facts containing the query.

        Searches:
        - Category
        - Entity
        - Attribute
        - Value
        """
        pattern = f"%{query}%"

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    category,
                    entity,
                    attribute,
                    value,
                    priority,
                    confidence,
                    created,
                    last_used,
                    times_used
                FROM facts
                WHERE category LIKE ? COLLATE NOCASE
                   OR entity LIKE ? COLLATE NOCASE
                   OR attribute LIKE ? COLLATE NOCASE
                   OR value LIKE ? COLLATE NOCASE
                ORDER BY priority DESC, id ASC
                """,
                (
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                ),
            ).fetchall()

        return [
            self._row_to_fact(row)
            for row in rows
        ]

    def update_value(
        self,
        memory_id: int,
        new_value: str
    ) -> bool:
        """
        Changes the value of an existing fact.

        Returns True if the memory existed and was updated.
        Returns False if no matching memory was found.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE facts
                SET value = ?
                WHERE id = ?
                """,
                (
                    new_value,
                    memory_id,
                ),
            )

            return cursor.rowcount > 0

    def delete(self, memory_id: int) -> bool:
        """
        Deletes a fact using its memory ID.

        Returns True if the memory was deleted.
        Returns False if the memory did not exist.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM facts
                WHERE id = ?
                """,
                (memory_id,),
            )

            return cursor.rowcount > 0