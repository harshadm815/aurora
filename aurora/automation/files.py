"""
Tidying a folder you have allowed: "organise my documents by type / by month".

Rules, all checked here:

- Only folders in ``settings.ORGANIZE_FOLDERS`` (default: the Documents
  folder). Only files directly in that folder are moved, and only into
  subfolders of the same folder.
- **Nothing is deleted or overwritten.** A name already taken gets
  " (2)", " (3)"…; a file that changed since the plan was made is left
  alone; a move onto an existing file is refused.
- Programs, scripts, shortcuts, hidden and system files, links and
  folders are never moved. Files of unknown types stay where they are.
- Every move is planned and shown first (AURORA asks before moving), then
  recorded in the automation database as it happens, so the whole tidy-up
  can be undone, even after AURORA restarts.
- It stops at the first error (and can be stopped by you part way); what
  was already moved is reported and can be undone.
"""

import logging
import os
import stat
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aurora.automation.store import AutomationStore
from aurora.config import settings

logger = logging.getLogger(__name__)

CATEGORIES: dict[str, set[str]] = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".svg"},
    "PDFs": {".pdf"},
    "Documents": {".doc", ".docx", ".odt", ".rtf"},
    "Notes": {".txt", ".md"},
    "Spreadsheets": {".xls", ".xlsx", ".ods", ".csv"},
    "Presentations": {".ppt", ".pptx", ".odp"},
    "Audio": {".mp3", ".wav", ".m4a", ".flac", ".ogg"},
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".webm"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
}
# Never moved, whatever the scheme: moving these could break something.
NEVER_MOVE = {".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".lnk", ".url", ".dll", ".sys", ".ini",
              ".com", ".scr", ".jar", ".reg", ".db", ".sqlite", ".tmp", ".part", ".crdownload"}
SCHEMES = ("type", "month")
SKIP_REASONS = {
    "other": ("file of another type", "files of other types"),
    "program": ("program, shortcut or system file", "programs, shortcuts or system files"),
    "hidden": ("hidden file", "hidden files"),
    "link": ("link", "links"),
    "unreadable": ("unreadable file", "unreadable files"),
}


class FileError(Exception):
    """Shown to the user as it is."""


def allowed_folders() -> list[Path]:
    folders = settings.ORGANIZE_FOLDERS if settings.ORGANIZE_FOLDERS is not None else [settings.DOCUMENTS_DIR]
    return [Path(folder).resolve() for folder in folders]


def find_folder(name: str = "") -> Path:
    """An allowed folder by name ("documents", "downloads"); the first one if no name is given."""
    folders = allowed_folders()
    if not folders:
        raise FileError("No folders are allowed to be tidied (ORGANIZE_FOLDERS in settings).")
    wanted = name.strip().lower()
    for prefix in ("my ", "the "):
        wanted = wanted.removeprefix(prefix)
    wanted = wanted.removesuffix(" folder").strip()
    if wanted in ("", "files", "folder"):
        return folders[0]
    for folder in folders:
        if folder.name.lower().rstrip("s") == wanted.rstrip("s"):      # "document" finds "Documents"
            return folder
    names = ", ".join(folder.name for folder in folders)
    raise FileError(f"I'm only allowed to tidy: {names}. (You can add folders to ORGANIZE_FOLDERS in settings.)")


@dataclass(frozen=True)
class Move:
    source: Path
    destination: Path
    size: int
    mtime: float


@dataclass
class Plan:
    folder: Path
    scheme: str
    moves: list[Move] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)     # reason -> count

    def groups(self) -> Counter:
        return Counter(move.destination.parent.name for move in self.moves)

    def summary(self) -> str:
        groups = self.groups()
        parts = ", ".join(f"{name} ({count})" for name, count in sorted(groups.items(), key=lambda g: (-g[1], g[0])))
        files = f"{len(self.moves)} file{'s' if len(self.moves) != 1 else ''}"
        folders = f"{len(groups)} folder{'s' if len(groups) != 1 else ''}"
        return f"{files} in {self.folder.name} into {folders}: {parts}"

    def skipped_note(self) -> str:
        if not self.skipped:
            return ""
        parts = [f"{count} {SKIP_REASONS[reason][count != 1]}" for reason, count in self.skipped.items()]
        return "Left where they are: " + ", ".join(parts) + "."


def _hidden(path: Path, info: os.stat_result) -> bool:
    if path.name.startswith("."):
        return True
    flags = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0)
    return bool(getattr(info, "st_file_attributes", 0) & flags)


def _free_name(folder: Path, name: str, taken: set[Path]) -> Path:
    candidate = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    number = 2
    while candidate.exists() or candidate in taken:
        candidate = folder / f"{stem} ({number}){suffix}"
        number += 1
    return candidate


def make_plan(folder: Path, scheme: str = "type") -> Plan:
    if scheme not in SCHEMES:
        raise FileError("I can tidy by type or by month.")
    folder = folder.resolve()
    if folder not in allowed_folders():
        raise FileError(f"I'm not allowed to tidy {folder.name}.")
    if not folder.is_dir():
        raise FileError(f"The folder {folder} doesn't exist.")

    plan = Plan(folder, scheme)
    taken: set[Path] = set()
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        try:
            info = path.lstat()
        except OSError:
            plan.skipped["unreadable"] += 1
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            plan.skipped["link"] += 1
            continue
        if stat.S_ISDIR(info.st_mode):
            continue                                  # folders are never moved
        if not stat.S_ISREG(info.st_mode):
            continue
        if _hidden(path, info):
            plan.skipped["hidden"] += 1
            continue
        suffix = path.suffix.lower()
        if suffix in NEVER_MOVE:
            plan.skipped["program"] += 1
            continue
        if scheme == "type":
            group = next((name for name, suffixes in CATEGORIES.items() if suffix in suffixes), None)
            if group is None:
                plan.skipped["other"] += 1
                continue
        else:
            group = f"{datetime.fromtimestamp(info.st_mtime):%Y-%m}"
        destination = _free_name(folder / group, path.name, taken)
        taken.add(destination)
        plan.moves.append(Move(path, destination, info.st_size, info.st_mtime))

    if len(plan.moves) > settings.ORGANIZE_MAX_FILES:
        raise FileError(f"That would move {len(plan.moves)} files; I only tidy up to "
                        f"{settings.ORGANIZE_MAX_FILES} at once (ORGANIZE_MAX_FILES).")
    return plan


@dataclass
class Outcome:
    batch: str
    planned: int
    moved: int = 0
    error: str = ""
    cancelled: bool = False


class Organizer:

    def __init__(self, store: AutomationStore | None = None):
        self._store = store
        self.cancel_event = threading.Event()

    @property
    def store(self) -> AutomationStore:
        if self._store is None:
            self._store = AutomationStore()
        return self._store

    def cancel(self) -> None:
        """Stops a tidy-up after the file being moved now."""
        self.cancel_event.set()

    def execute(self, plan: Plan) -> Outcome:
        self.cancel_event.clear()
        outcome = Outcome(uuid.uuid4().hex[:12], len(plan.moves))
        root = plan.folder
        for move in plan.moves:
            if self.cancel_event.is_set():
                outcome.cancelled = True
                break
            try:
                self._move(move, root, outcome.batch)
            except (OSError, FileError) as error:
                reason = error if isinstance(error, FileError) else (error.strerror or type(error).__name__)
                outcome.error = f"{move.source.name} couldn't be moved ({reason})"
                break
            outcome.moved += 1
        logger.info("Tidy-up %s: %d of %d files moved%s", outcome.batch, outcome.moved, outcome.planned,
                    ", stopped" if outcome.error or outcome.cancelled else "")
        return outcome

    def _move(self, move: Move, root: Path, batch: str) -> None:
        source, destination = move.source, move.destination
        if destination.parent.parent != root or source.parent != root:
            raise FileError("it would leave the allowed folder")
        try:
            info = source.lstat()
        except FileNotFoundError:
            raise FileError("it is no longer there") from None
        if info.st_size != move.size or info.st_mtime != move.mtime:
            raise FileError("it changed after the plan was made")
        if destination.exists():
            raise FileError(f"{destination.parent.name}/{destination.name} already exists")
        created = None
        if not destination.parent.exists():
            destination.parent.mkdir()
            created = str(destination.parent)
        elif not destination.parent.is_dir() or destination.parent.is_symlink():
            raise FileError(f"{destination.parent.name} isn't a normal folder")
        os.rename(source, destination)       # same folder tree: a rename, never a copy
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO file_moves (batch, source, destination, created_folder, time) VALUES (?, ?, ?, ?, ?)",
                (batch, str(source), str(destination), created, datetime.now().isoformat(timespec="seconds")))

    # --- Undo -----------------------------------------------------------------------------

    def last_batch(self) -> tuple[str, int, str] | None:
        """(batch, files, folder) of the newest tidy-up that hasn't been undone."""
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT batch, COUNT(*) AS n, MIN(source) AS source FROM file_moves WHERE undone = 0 "
                "GROUP BY batch ORDER BY MAX(id) DESC LIMIT 1").fetchone()
        return (row["batch"], row["n"], Path(row["source"]).parent.name) if row else None

    def undo(self, batch: str) -> tuple[int, list[str]]:
        """Moves a tidy-up's files back (newest first). Returns (moved back, problems)."""
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT id, source, destination, created_folder FROM file_moves WHERE batch = ? AND undone = 0 "
                "ORDER BY id DESC", (batch,)).fetchall()
        back, problems, created = 0, [], []
        allowed = allowed_folders()
        for row in rows:
            source, destination = Path(row["source"]), Path(row["destination"])
            if row["created_folder"]:
                created.append(Path(row["created_folder"]))
            if source.parent not in allowed:
                problems.append(f"{source.name}: its folder is no longer allowed")
                continue
            if not destination.exists():
                problems.append(f"{destination.name} is no longer in {destination.parent.name}")
                continue
            if source.exists():
                problems.append(f"{source.name} already exists again in {source.parent.name}")
                continue
            try:
                os.rename(destination, source)
            except OSError as error:
                problems.append(f"{destination.name} couldn't be moved back ({error.strerror or type(error).__name__})")
                continue
            with self.store.connect() as connection:
                connection.execute("UPDATE file_moves SET undone = 1 WHERE id = ?", (row["id"],))
            back += 1
        for folder in created:
            try:
                folder.rmdir()                  # only if empty; never removes anything else
            except OSError:
                pass
        logger.info("Tidy-up %s undone: %d files back, %d problems", batch, back, len(problems))
        return back, problems
