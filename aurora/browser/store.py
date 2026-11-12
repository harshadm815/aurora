"""
Browser history and bookmarks, in BROWSER_DATABASE_PATH on this PC only
(not in git, not in the OneDrive backup).

    history     one row per visit: address, title, time. Only http/https
                pages; not recorded while BROWSER_HISTORY is off; entries
                older than BROWSER_HISTORY_DAYS are deleted.
    bookmarks   address, title, folder, when added
    reading     the reading list: pages saved to read later (address, title, added, read)
    downloads   files downloaded (file name, where it was saved, the site, when, how it ended)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from aurora.browser.addresses import is_web, without_fragment
from aurora.config import settings
from aurora.memory.store import connect

MAX_TITLE = 300
MAX_URL = 4000


@dataclass(frozen=True)
class Visit:
    id: int
    url: str
    title: str
    time: datetime


@dataclass(frozen=True)
class ReadingItem:
    id: int
    url: str
    title: str
    added: datetime
    read: bool


@dataclass(frozen=True)
class Download:
    id: int
    name: str
    path: str
    host: str
    time: datetime
    state: str             # "downloading", "completed", "cancelled", "failed"


@dataclass(frozen=True)
class Bookmark:
    id: int
    url: str
    title: str
    folder: str
    added: datetime


class BrowserStore:

    def __init__(self, path: Path | None = None, clock=datetime.now):
        self.path = Path(path or settings.BROWSER_DATABASE_PATH)
        self.clock = clock
        with connect(self.path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL, time TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS history_time ON history (time);
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    folder TEXT NOT NULL, added TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reading (
                    id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    added TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL, host TEXT NOT NULL,
                    time TEXT NOT NULL, state TEXT NOT NULL
                );
                """
            )

    # ---------------------------------------------------------------- history

    def record_visit(self, url: str, title: str = "") -> bool:
        """Adds a visit (or updates the title of the same page just visited). False if not recorded."""
        if not settings.BROWSER_HISTORY or not is_web(url) or len(url) > MAX_URL:
            return False
        url, title, now = without_fragment(url), (title or "").strip()[:MAX_TITLE], self.clock()
        with connect(self.path) as connection:
            last = connection.execute("SELECT id, url, time FROM history ORDER BY id DESC LIMIT 1").fetchone()
            if last and last[1] == url and now - datetime.fromisoformat(last[2]) < timedelta(minutes=30):
                if title:                      # the same page again (reload, title arriving later)
                    connection.execute("UPDATE history SET title = ? WHERE id = ?", (title, last[0]))
                return True
            connection.execute("INSERT INTO history (url, title, time) VALUES (?, ?, ?)",
                               (url, title or url, now.isoformat()))
        return True

    def history(self, search: str = "", limit: int = 200) -> list[Visit]:
        sql, params = "SELECT id, url, title, time FROM history", []
        if search.strip():
            sql += " WHERE url LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\'"
            pattern = "%" + search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params = [pattern, pattern]
        sql += " ORDER BY time DESC, id DESC LIMIT ?"
        with connect(self.path) as connection:
            rows = connection.execute(sql, params + [limit]).fetchall()
        return [Visit(r[0], r[1], r[2], datetime.fromisoformat(r[3])) for r in rows]

    def delete_visit(self, visit_id: int) -> None:
        with connect(self.path) as connection:
            connection.execute("DELETE FROM history WHERE id = ?", (visit_id,))

    def clear_history(self) -> int:
        with connect(self.path) as connection:
            return connection.execute("DELETE FROM history").rowcount

    def prune(self, days: int | None = None) -> int:
        """Deletes history older than BROWSER_HISTORY_DAYS. Returns how many visits."""
        days = settings.BROWSER_HISTORY_DAYS if days is None else days
        cutoff = (self.clock() - timedelta(days=days)).isoformat()
        with connect(self.path) as connection:
            return connection.execute("DELETE FROM history WHERE time < ?", (cutoff,)).rowcount

    def suggestions(self, typed: str, limit: int = 8) -> list[tuple[str, str]]:
        """(url, title) of bookmarks, then visited pages, matching what's typed."""
        typed = typed.strip().lower()
        if not typed:
            return []
        found: dict[str, str] = {}
        for bookmark in self.bookmarks():
            if typed in bookmark.url.lower() or typed in bookmark.title.lower():
                found.setdefault(bookmark.url, bookmark.title)
        for visit in self.history(typed, limit=50):
            found.setdefault(visit.url, visit.title)
        return list(found.items())[:limit]

    # -------------------------------------------------------------- bookmarks

    def add_bookmark(self, url: str, title: str = "", folder: str = "Bookmarks") -> bool:
        """True if added, False if it was already bookmarked (its title and folder are updated)."""
        if not is_web(url) or len(url) > MAX_URL:
            raise ValueError("Only web pages (http and https) can be bookmarked.")
        url = without_fragment(url)
        title = (title or "").strip()[:MAX_TITLE] or url
        folder = (folder or "").strip()[:80] or "Bookmarks"
        with connect(self.path) as connection:
            existing = connection.execute("SELECT id FROM bookmarks WHERE url = ?", (url,)).fetchone()
            if existing:
                connection.execute("UPDATE bookmarks SET title = ?, folder = ? WHERE id = ?",
                                   (title, folder, existing[0]))
                return False
            connection.execute("INSERT INTO bookmarks (url, title, folder, added) VALUES (?, ?, ?, ?)",
                               (url, title, folder, self.clock().isoformat()))
        return True

    def bookmarks(self, folder: str | None = None) -> list[Bookmark]:
        sql = "SELECT id, url, title, folder, added FROM bookmarks"
        params: tuple = ()
        if folder:
            sql, params = sql + " WHERE folder = ?", (folder,)
        with connect(self.path) as connection:
            rows = connection.execute(sql + " ORDER BY folder, title COLLATE NOCASE", params).fetchall()
        return [Bookmark(r[0], r[1], r[2], r[3], datetime.fromisoformat(r[4])) for r in rows]

    def bookmark_for(self, url: str) -> Bookmark | None:
        if not is_web(url):
            return None
        url = without_fragment(url)
        return next((b for b in self.bookmarks() if b.url == url), None)

    def remove_bookmark(self, bookmark_id: int) -> None:
        with connect(self.path) as connection:
            connection.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))

    # ----------------------------------------------------------- reading list

    def add_to_reading_list(self, url: str, title: str = "") -> bool:
        """True if added; False if it was already on the list (marked unread again)."""
        if not is_web(url) or len(url) > MAX_URL:
            raise ValueError("Only web pages (http and https) can go on the reading list.")
        url = without_fragment(url)
        with connect(self.path) as connection:
            if connection.execute("SELECT id FROM reading WHERE url = ?", (url,)).fetchone():
                connection.execute("UPDATE reading SET read = 0 WHERE url = ?", (url,))
                return False
            connection.execute("INSERT INTO reading (url, title, added, read) VALUES (?, ?, ?, 0)",
                               (url, (title or "").strip()[:MAX_TITLE] or url, self.clock().isoformat()))
        return True

    def reading_list(self, include_read: bool = True) -> list[ReadingItem]:
        sql = "SELECT id, url, title, added, read FROM reading"
        if not include_read:
            sql += " WHERE read = 0"
        with connect(self.path) as connection:
            rows = connection.execute(sql + " ORDER BY read, added DESC").fetchall()
        return [ReadingItem(r[0], r[1], r[2], datetime.fromisoformat(r[3]), bool(r[4])) for r in rows]

    def mark_read(self, item_id: int, read: bool = True) -> None:
        with connect(self.path) as connection:
            connection.execute("UPDATE reading SET read = ? WHERE id = ?", (int(read), item_id))

    def remove_from_reading_list(self, item_id: int) -> None:
        with connect(self.path) as connection:
            connection.execute("DELETE FROM reading WHERE id = ?", (item_id,))

    # -------------------------------------------------------------- downloads

    def record_download(self, name: str, path: str, host: str, state: str = "downloading") -> int:
        with connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT INTO downloads (name, path, host, time, state) VALUES (?, ?, ?, ?, ?)",
                (name[:MAX_TITLE], path[:MAX_URL], host[:300], self.clock().isoformat(), state))
            return cursor.lastrowid

    def update_download(self, download_id: int, state: str) -> None:
        with connect(self.path) as connection:
            connection.execute("UPDATE downloads SET state = ? WHERE id = ?", (state, download_id))

    def downloads(self, limit: int = 100) -> list[Download]:
        with connect(self.path) as connection:
            rows = connection.execute("SELECT id, name, path, host, time, state FROM downloads "
                                      "ORDER BY time DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [Download(r[0], r[1], r[2], r[3], datetime.fromisoformat(r[4]), r[5]) for r in rows]

    def clear_downloads(self) -> int:
        """Forgets the list (the files themselves stay where they were saved)."""
        with connect(self.path) as connection:
            return connection.execute("DELETE FROM downloads WHERE state != 'downloading'").rowcount
