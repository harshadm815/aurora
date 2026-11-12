"""Favourites and recent files for the Documents page (EXPLORER_DATABASE_PATH, on this PC only)."""

from datetime import datetime
from pathlib import Path

from aurora.config import settings
from aurora.memory.store import connect

RECENT_LIMIT = 50


class ExplorerStore:

    def __init__(self, path: Path | None = None, clock=datetime.now):
        self.path = Path(path or settings.EXPLORER_DATABASE_PATH)
        self.clock = clock
        with connect(self.path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS favourites (path TEXT PRIMARY KEY, added TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recent (path TEXT PRIMARY KEY, opened TEXT NOT NULL);
                """
            )

    def favourites(self) -> list[Path]:
        with connect(self.path) as connection:
            rows = connection.execute("SELECT path FROM favourites ORDER BY added").fetchall()
        return [Path(r[0]) for r in rows if Path(r[0]).exists()]

    def is_favourite(self, path: Path) -> bool:
        with connect(self.path) as connection:
            return connection.execute("SELECT 1 FROM favourites WHERE path = ?", (str(path),)).fetchone() is not None

    def set_favourite(self, path: Path, on: bool) -> None:
        with connect(self.path) as connection:
            if on:
                connection.execute("INSERT OR IGNORE INTO favourites (path, added) VALUES (?, ?)",
                                   (str(path), self.clock().isoformat()))
            else:
                connection.execute("DELETE FROM favourites WHERE path = ?", (str(path),))

    def opened(self, path: Path) -> None:
        with connect(self.path) as connection:
            connection.execute("INSERT INTO recent (path, opened) VALUES (?, ?) "
                               "ON CONFLICT(path) DO UPDATE SET opened = excluded.opened",
                               (str(path), self.clock().isoformat()))
            connection.execute("DELETE FROM recent WHERE path NOT IN "
                               "(SELECT path FROM recent ORDER BY opened DESC LIMIT ?)", (RECENT_LIMIT,))

    def recent(self, limit: int = 30) -> list[Path]:
        with connect(self.path) as connection:
            rows = connection.execute("SELECT path FROM recent ORDER BY opened DESC LIMIT ?", (limit,)).fetchall()
        return [Path(r[0]) for r in rows if Path(r[0]).exists()]

    def forget(self, path: Path) -> None:
        """After a rename or delete: drop the old path everywhere."""
        with connect(self.path) as connection:
            connection.execute("DELETE FROM favourites WHERE path = ?", (str(path),))
            connection.execute("DELETE FROM recent WHERE path = ?", (str(path),))

    def clear_recent(self) -> None:
        with connect(self.path) as connection:
            connection.execute("DELETE FROM recent")
