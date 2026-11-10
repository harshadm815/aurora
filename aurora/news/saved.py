"""
Saved news articles (News Centre): on this PC only, in NEWS_DATABASE_PATH.
Each has a folder for organising and an optional note. Only what the
publisher's feed or the search service said is kept, plus your folder and
note; article pages aren't downloaded.
"""

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aurora.config import settings
from aurora.news.article import Article
from aurora.news.processing import canonical_url

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_articles (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    publisher TEXT NOT NULL,
    published TEXT,
    summary TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT '',
    folder TEXT NOT NULL DEFAULT 'Saved',
    note TEXT NOT NULL DEFAULT '',
    saved_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class SavedArticle:
    id: int
    url: str
    title: str
    publisher: str
    published: datetime | None
    summary: str
    method: str
    folder: str
    note: str
    saved_at: datetime


class SavedArticles:

    def __init__(self, path: Path | None = None):
        self.path = Path(path or settings.NEWS_DATABASE_PATH)
        self._lock = threading.Lock()
        with self._connect() as connection:
            connection.execute(SCHEMA)

    @contextmanager
    def _connect(self):
        """Commits on success and always closes (Windows keeps open files locked)."""
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, article: Article, folder: str = "Saved", note: str = "") -> bool:
        """Saves an article (False if it was already saved; its folder and note are then updated)."""
        folder = (folder or "Saved").strip()[:60] or "Saved"
        with self._lock, self._connect() as connection:
            existing = connection.execute("SELECT id FROM saved_articles WHERE url = ?",
                                          (canonical_url(article.url),)).fetchone()
            if existing:
                connection.execute("UPDATE saved_articles SET folder = ?, note = ? WHERE id = ?",
                                   (folder, note[:500], existing[0]))
                return False
            connection.execute(
                "INSERT INTO saved_articles (url, title, publisher, published, summary, method, folder, note, saved_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (canonical_url(article.url), article.title, article.publisher,
                 article.published.isoformat() if article.published else None, article.summary, article.method,
                 folder, note[:500], datetime.now().isoformat(timespec="seconds")))
            return True

    def all(self, folder: str | None = None) -> list[SavedArticle]:
        query = "SELECT * FROM saved_articles" + (" WHERE folder = ?" if folder else "") + " ORDER BY saved_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, (folder,) if folder else ()).fetchall()
        return [SavedArticle(r[0], r[1], r[2], r[3], datetime.fromisoformat(r[4]) if r[4] else None, r[5], r[6],
                             r[7], r[8], datetime.fromisoformat(r[9])) for r in rows]

    def folders(self) -> list[str]:
        with self._connect() as connection:
            return [r[0] for r in connection.execute("SELECT DISTINCT folder FROM saved_articles ORDER BY folder")]

    def move(self, article_id: int, folder: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("UPDATE saved_articles SET folder = ? WHERE id = ?",
                               ((folder or "Saved").strip()[:60] or "Saved", article_id))

    def delete(self, article_id: int) -> bool:
        with self._lock, self._connect() as connection:
            return bool(connection.execute("DELETE FROM saved_articles WHERE id = ?", (article_id,)).rowcount)
