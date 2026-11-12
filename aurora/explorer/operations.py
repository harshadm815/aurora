"""
File operations for the Documents page, with Windows File Explorer's
rules and a few stricter ones:

- Every path must be inside one of the places (aurora/explorer/places.py);
  anything else is refused, so system folders can't be touched.
- Nothing is ever overwritten. A pasted or new item whose name is taken
  gets " (2)", " (3)"…; a rename to a taken name is refused.
- Delete moves items to the Recycle Bin, so they can be restored.
- Names Windows can't use (\\ / : * ? " < > |, CON, PRN…, trailing dots
  or spaces) are refused with the reason.
"""

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aurora.explorer.places import place_of

TYPE_NAMES = {
    ".pdf": "PDF document", ".md": "Markdown document", ".txt": "Text document", ".docx": "Word document",
    ".doc": "Word document", ".xlsx": "Excel spreadsheet", ".csv": "CSV spreadsheet", ".pptx": "PowerPoint",
    ".jpg": "JPEG image", ".jpeg": "JPEG image", ".png": "PNG image", ".gif": "GIF image", ".webp": "WebP image",
    ".bmp": "Bitmap image", ".svg": "SVG image", ".mp4": "MP4 video", ".mkv": "MKV video", ".avi": "AVI video",
    ".mov": "QuickTime video", ".mp3": "MP3 audio", ".wav": "WAV audio", ".flac": "FLAC audio", ".m4a": "M4A audio",
    ".zip": "ZIP archive", ".py": "Python script", ".json": "JSON file", ".html": "Web page", ".tex": "LaTeX document",
    ".exe": "Application", ".lnk": "Shortcut",
}
TEXT_PREVIEW = {".txt", ".md", ".py", ".csv", ".json", ".tex", ".log", ".ini", ".yaml", ".yml", ".html", ".xml"}
IMAGE_PREVIEW = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
PREVIEW_BYTES = 2_000_000
_FORBIDDEN = set('\\/:*?"<>|')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class ExplorerError(Exception):
    """An operation that wasn't done, with the reason (shown to you as is)."""


@dataclass(frozen=True)
class Info:
    name: str
    path: Path
    type: str
    size: int | None          # bytes; None for folders
    created: datetime
    modified: datetime
    is_dir: bool

    @property
    def location(self) -> str:
        return str(self.path.parent)


def type_name(path: Path) -> str:
    if path.is_dir():
        return "File folder"
    suffix = path.suffix.lower()
    return TYPE_NAMES.get(suffix, f"{suffix[1:].upper()} file" if suffix else "File")


def human_size(size: int | None) -> str:
    if size is None:
        return ""
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def info(path: Path) -> Info:
    path = Path(path)
    stat = path.stat()
    return Info(path.name, path, type_name(path), None if path.is_dir() else stat.st_size,
                datetime.fromtimestamp(stat.st_ctime), datetime.fromtimestamp(stat.st_mtime), path.is_dir())


def check_inside(path: Path, places=None) -> None:
    if place_of(path, places) is None:
        raise ExplorerError(f"{path} isn't in one of the Documents page's places, so it can't be changed here.")


def check_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ExplorerError("A name can't be empty.")
    if any(c in _FORBIDDEN for c in name) or any(ord(c) < 32 for c in name):
        raise ExplorerError('A name can\'t contain \\ / : * ? " < > |')
    if name.endswith((".", " ")):
        raise ExplorerError("A name can't end with a dot or a space.")
    if name.split(".")[0].lower() in _RESERVED:
        raise ExplorerError(f"Windows reserves the name {name.split('.')[0]}.")
    if len(name) > 200:
        raise ExplorerError("That name is too long.")
    return name


def free_name(folder: Path, name: str) -> Path:
    """folder/name, or folder/name (2), (3)… if it's taken."""
    target = folder / name
    if not target.exists():
        return target
    stem, suffix = (Path(name).stem, Path(name).suffix) if not (folder / name).is_dir() else (name, "")
    number = 2
    while (folder / f"{stem} ({number}){suffix}").exists():
        number += 1
    return folder / f"{stem} ({number}){suffix}"


def new_folder(parent: Path, name: str = "New folder", places=None) -> Path:
    parent = Path(parent)
    check_inside(parent, places)
    target = free_name(parent, check_name(name))
    target.mkdir()
    return target


def new_document(parent: Path, name: str = "New document.md", places=None) -> Path:
    parent = Path(parent)
    check_inside(parent, places)
    name = check_name(name)
    if not Path(name).suffix:
        name += ".md"
    target = free_name(parent, name)
    with open(target, "x", encoding="utf-8") as handle:
        handle.write(f"# {target.stem}\n\n" if target.suffix.lower() == ".md" else "")
    return target


def rename(path: Path, new_name: str, places=None) -> Path:
    path = Path(path)
    check_inside(path, places)
    new_name = check_name(new_name)
    target = path.with_name(new_name)
    if new_name == path.name:            # exact: Windows paths compare equal ignoring case
        return path
    if target.exists() and target.resolve() != path.resolve():      # (a change of letter case is fine)
        raise ExplorerError(f"There's already an item called “{new_name}” here.")
    path.rename(target)
    return target


def paste(sources: list[Path], destination: Path, cut: bool, places=None) -> list[Path]:
    """Copies (or moves, if cut) sources into destination. Returns the new paths."""
    destination = Path(destination)
    check_inside(destination, places)
    if not destination.is_dir():
        raise ExplorerError("Paste into a folder.")
    done = []
    for source in map(Path, sources):
        check_inside(source, places)
        if not source.exists():
            raise ExplorerError(f"“{source.name}” no longer exists.")
        if source.is_dir() and (destination.resolve() == source.resolve()
                                or destination.resolve().is_relative_to(source.resolve())):
            raise ExplorerError(f"“{source.name}” can't go inside itself.")
        if cut and source.parent.resolve() == destination.resolve():
            done.append(source)                       # moving to where it already is
            continue
        target = free_name(destination, source.name)
        if cut:
            shutil.move(str(source), str(target))
        elif source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
        done.append(target)
    return done


def _recycle(path: Path) -> bool:
    from PySide6.QtCore import QFile
    return bool(QFile.moveToTrash(str(path)))


def to_recycle_bin(paths: list[Path], places=None, mover=_recycle) -> list[Path]:
    """Moves items to the Recycle Bin. Returns the ones moved; raises if any couldn't be."""
    moved, failed = [], []
    for path in map(Path, paths):
        check_inside(path, places)
        if place_of(path, places).path.resolve() == path.resolve():
            raise ExplorerError(f"{path.name} is one of the places itself and can't be deleted here.")
        (moved if mover(path) else failed).append(path)
    if failed:
        raise ExplorerError("Couldn't move to the Recycle Bin: " + ", ".join(p.name for p in failed))
    return moved


def search_names(root: Path, text: str, limit: int = 200) -> list[Path]:
    """Files and folders under root whose names contain text (case doesn't matter)."""
    text = (text or "").strip().lower()
    if not text:
        return []
    found = []
    for path in Path(root).rglob("*"):
        if path.name.startswith(("." , "~$")):
            continue
        if text in path.name.lower():
            found.append(path)
            if len(found) >= limit:
                break
    return found


def preview_text(path: Path, limit: int = 4000) -> str | None:
    """The start of a text or PDF file for the preview panel, or None if there's none to show."""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if path.stat().st_size > PREVIEW_BYTES * (10 if suffix == ".pdf" else 1):
            return None
        if suffix in TEXT_PREVIEW:
            with open(path, encoding="utf-8", errors="replace") as handle:
                return handle.read(limit)
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            text = ""
            for page in reader.pages[:3]:
                text += (page.extract_text() or "") + "\n"
                if len(text) >= limit:
                    break
            return text[:limit].strip() or None
    except Exception:              # damaged or locked files just have no preview
        return None
    return None
