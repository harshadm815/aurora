"""
Sensor history in SQLite (``settings.SENSOR_DATABASE_PATH``).

Room readings and health readings are kept in separate tables, with
separate retention: health data is deleted after
``HEALTH_RETENTION_DAYS`` (7), room data after ``SENSOR_RETENTION_DAYS``
(30). Neither is ever mixed into AURORA's long-term memory.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from aurora.config import settings
from aurora.sensors.reading import Reading

_TABLES = {False: "readings", True: "health_readings"}


class SensorStore:

    def __init__(self, path: str | Path | None = None):
        self.path = path or settings.SENSOR_DATABASE_PATH
        with self._connect() as connection:
            for table in _TABLES.values():
                connection.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        time TEXT NOT NULL,
                        quantity TEXT NOT NULL,
                        value REAL NOT NULL,
                        source TEXT NOT NULL,
                        location TEXT NOT NULL,
                        simulated INTEGER NOT NULL
                    )
                    """
                )
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_quantity_time ON {table} (quantity, time)"
                )

    def _connect(self):
        return sqlite3.connect(self.path)

    def add(self, readings: list[Reading]) -> None:
        with self._connect() as connection:
            for reading in readings:
                connection.execute(
                    f"INSERT INTO {_TABLES[reading.is_health]} "
                    "(time, quantity, value, source, location, simulated) VALUES (?, ?, ?, ?, ?, ?)",
                    (reading.time.isoformat(), reading.quantity, float(reading.value),
                     reading.source, reading.location, int(reading.simulated)),
                )
        self.purge()

    def latest(self, quantity: str, health: bool = False, max_age: timedelta | None = None) -> Reading | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT time, quantity, value, source, location, simulated FROM {_TABLES[health]} "
                "WHERE quantity = ? ORDER BY time DESC LIMIT 1",
                (quantity,),
            ).fetchone()
        if row is None:
            return None
        reading = self._reading(row)
        if max_age is not None and datetime.now() - reading.time > max_age:
            return None
        return reading

    def count(self, health: bool = False) -> int:
        with self._connect() as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {_TABLES[health]}").fetchone()[0]

    def purge(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        limits = {
            False: now - timedelta(days=settings.SENSOR_RETENTION_DAYS),
            True: now - timedelta(days=settings.HEALTH_RETENTION_DAYS),
        }
        with self._connect() as connection:
            for health, cutoff in limits.items():
                connection.execute(f"DELETE FROM {_TABLES[health]} WHERE time < ?", (cutoff.isoformat(),))

    def delete_health_data(self) -> int:
        with self._connect() as connection:
            return connection.execute(f"DELETE FROM {_TABLES[True]}").rowcount

    @staticmethod
    def _reading(row) -> Reading:
        time, quantity, value, source, location, simulated = row
        value = bool(value) if quantity == "motion" else value
        return Reading(quantity, value, source, location, bool(simulated), datetime.fromisoformat(time))
