"""
The knowledge index: the user's documents, split into passages and
indexed for full-text search (SQLite FTS5, Porter stemming, BM25 ranking).

    knowledge_index.db      (settings.KNOWLEDGE_INDEX_PATH; not in git)
        documents           one row per indexed file: size, modified time,
                            SHA-256, pages, passages, when indexed
        passages            FTS5 table: passage text + document, page, position

Ingestion goes through the Documents tool, so its safety rules apply:
only .txt / .md / .pdf files inside the Documents folder, links outside
it ignored, oversized files skipped. sync() only re-reads files whose
size, time or content changed, and drops files that were removed.

Passages: paragraphs of a page, merged up to about PASSAGE_WORDS words
(long paragraphs are split at sentence ends), so each passage has one
page number for citations.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aurora.config import settings
from aurora.memory.store import connect
from aurora.tool_plugins.documents import IMAGE_EXTENSIONS, DocumentsTool

logger = logging.getLogger(__name__)

PASSAGE_WORDS = 120
MAX_PASSAGE_WORDS = 220

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"“'(A-Z0-9])")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")

# Words that never help find a passage (the FTS index keeps them, queries don't use them).
QUERY_STOPWORDS = {
    "a", "an", "the", "my", "me", "i", "you", "your", "is", "are", "was", "were", "be", "been", "am",
    "of", "on", "in", "at", "to", "for", "and", "or", "what", "who", "where", "when", "which", "how",
    "why", "do", "did", "does", "that", "this", "it", "its", "about", "say", "says", "said", "tell",
    "according", "documents", "document", "notes", "note", "files", "file", "doc", "docs", "pdf",
    "with", "from", "by", "as", "there", "their", "they", "them", "he", "she", "we", "us", "our",
    "can", "could", "would", "should", "will", "please", "any", "some", "much", "many", "mention",
    "mentions", "mentioned", "written", "write", "wrote", "explain", "describe", "summarize", "summarise",
}


def split_sentences(text: str) -> list[str]:
    """Sentences, also breaking at line ends (headings, list items)."""
    sentences = []
    for line in (text or "").splitlines():
        line = " ".join(line.split()).lstrip("#*- ").strip()
        sentences += [s.strip() for s in _SENTENCE_END.split(line) if s.strip()]
    return sentences


def query_terms(question: str) -> list[str]:
    """Lower-case content words of a question, in order, without repeats."""
    words = [w.lower().strip("'-") for w in _WORD.findall(question or "")]
    return list(dict.fromkeys(w for w in words if w and w not in QUERY_STOPWORDS and len(w) > 1))


@dataclass(frozen=True)
class Hit:
    document: str
    page: int | None
    text: str
    score: float          # BM25 from SQLite: lower (more negative) is better
    position: int = 0


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    failed: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed)


def chunk(passages) -> list[tuple[int | None, str]]:
    """(page, text) passages of roughly PASSAGE_WORDS words, never crossing pages."""
    chunks: list[tuple[int | None, str]] = []
    current_page, current = None, []

    def flush():
        if current:
            # Paragraphs keep their line breaks, so a heading never runs into the next sentence.
            chunks.append((current_page, "\n".join(current)))
            current.clear()

    for passage in passages:
        text = " ".join(passage.text.split())
        if not text:
            continue
        if passage.page != current_page:
            flush()
            current_page = passage.page
        pieces = [text] if len(text.split()) <= MAX_PASSAGE_WORDS else split_sentences(text)
        for piece in pieces:
            if current and sum(len(p.split()) for p in current) + len(piece.split()) > PASSAGE_WORDS:
                flush()
            current.append(piece)
    flush()
    return chunks


class KnowledgeIndex:

    def __init__(self, documents: DocumentsTool | None = None, path: Path | None = None):
        self.documents = documents or DocumentsTool()
        self.path = Path(path or settings.KNOWLEDGE_INDEX_PATH)
        with connect(self.path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    name TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL, sha256 TEXT NOT NULL,
                    pages INTEGER, passages INTEGER NOT NULL, indexed TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(
                    text, document UNINDEXED, page UNINDEXED, position UNINDEXED,
                    tokenize = 'porter unicode61'
                );
                """
            )

    def _connect(self):
        return connect(self.path)

    # ------------------------------------------------------------ ingestion

    def sync(self) -> SyncReport:
        """Bring the index up to date with the Documents folder."""
        report = SyncReport()
        files, _ = self.documents.files()
        present = {}
        for path in files:
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                continue
            present[self.documents._name(path)] = path

        with self._connect() as connection:
            known = {row[0]: row[1:] for row in connection.execute("SELECT name, size, mtime, sha256 FROM documents")}

        for name in sorted(set(known) - set(present)):
            self._remove(name)
            report.removed.append(name)

        for name, path in present.items():
            stat = path.stat()
            old = known.get(name)
            if old and old[0] == stat.st_size and abs(old[1] - stat.st_mtime) < 1e-6:
                report.unchanged += 1
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if old and old[2] == digest:
                    with self._connect() as connection:
                        connection.execute("UPDATE documents SET size = ?, mtime = ? WHERE name = ?",
                                           (stat.st_size, stat.st_mtime, name))
                    report.unchanged += 1
                    continue
                self._index(name, path, stat, digest)
                (report.updated if old else report.added).append(name)
            except Exception as error:   # a damaged PDF must not stop the rest
                logger.warning("Indexing a document failed: %s", type(error).__name__)
                report.failed.append(name)
        if report.changed:
            logger.info("Knowledge index: %d added, %d updated, %d removed",
                        len(report.added), len(report.updated), len(report.removed))
        return report

    def _index(self, name: str, path: Path, stat, digest: str) -> None:
        passages = self.documents.passages(path)
        chunks = chunk(passages)
        pages = max((p.page or 0 for p in passages), default=0) or None
        with self._connect() as connection:
            connection.execute("DELETE FROM passages WHERE document = ?", (name,))
            connection.executemany(
                "INSERT INTO passages (text, document, page, position) VALUES (?, ?, ?, ?)",
                [(text, name, page, position) for position, (page, text) in enumerate(chunks)])
            connection.execute(
                "INSERT INTO documents (name, size, mtime, sha256, pages, passages, indexed) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET size = excluded.size, mtime = excluded.mtime, "
                "sha256 = excluded.sha256, pages = excluded.pages, passages = excluded.passages, indexed = excluded.indexed",
                (name, stat.st_size, stat.st_mtime, digest, pages, len(chunks), datetime.now().isoformat()))

    def _remove(self, name: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM passages WHERE document = ?", (name,))
            connection.execute("DELETE FROM documents WHERE name = ?", (name,))

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM passages")
            connection.execute("DELETE FROM documents")

    # ------------------------------------------------------------- reading

    def documents_indexed(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, pages, passages, indexed, size FROM documents ORDER BY name").fetchall()
        return [{"name": r[0], "pages": r[1], "passages": r[2], "indexed": datetime.fromisoformat(r[3]),
                 "size": r[4]} for r in rows]

    def find_document(self, wanted: str) -> list[str]:
        """Indexed document names matching what the user called it ("thesis" -> thesis.pdf)."""
        wanted = (wanted or "").strip().lower()
        names = [d["name"] for d in self.documents_indexed()]
        exact = [n for n in names if n.lower() == wanted or Path(n).name.lower() == wanted
                 or Path(n).stem.lower() == wanted]
        return exact or [n for n in names if wanted and wanted in n.lower()]

    def search(self, question: str, limit: int = 6, document: str | None = None) -> list[Hit]:
        terms = query_terms(question)
        if not terms:
            return []
        match = " OR ".join('"' + t.replace('"', "") + '"' for t in terms)
        sql = ("SELECT document, page, text, bm25(passages), position FROM passages WHERE passages MATCH ?"
               + (" AND document = ?" if document else "") + " ORDER BY bm25(passages) LIMIT ?")
        params = (match, document, limit) if document else (match, limit)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [Hit(r[0], r[1], r[2], r[3], r[4]) for r in rows]

    def passages_of(self, document: str) -> list[Hit]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT document, page, text, 0, position FROM passages WHERE document = ? ORDER BY position",
                (document,)).fetchall()
        return [Hit(r[0], r[1], r[2], 0.0, r[4]) for r in rows]

    def document_frequency(self, term: str) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM passages WHERE passages MATCH ?",
                                      ('"' + term.replace('"', "") + '"',)).fetchone()[0]

    def passage_count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
