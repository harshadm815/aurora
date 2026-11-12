"""The folders the Documents page shows (and the only ones it can change)."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from aurora.config import settings

AURORA = "aurora"          # AURORA's Documents folder: indexed, Smart search, summaries
USER = "user"              # a Windows library folder
CLOUD = "cloud"            # OneDrive

# Windows CSIDL numbers (SHGetFolderPathW): the real folders, also when redirected to OneDrive.
_CSIDL = {"Documents": 0x05, "Pictures": 0x27, "Videos": 0x0E, "Music": 0x0D, "Desktop": 0x10}
ICONS = {"AURORA Documents": "✦", "Documents": "📄", "Pictures": "🖼", "Videos": "🎬", "Music": "🎵",
         "Downloads": "⬇", "Desktop": "🖥", "OneDrive": "☁"}


@dataclass(frozen=True)
class Place:
    name: str
    path: Path
    kind: str

    @property
    def icon(self) -> str:
        return ICONS.get(self.name, "📁")


def known_folder(name: str) -> Path | None:
    if name == "Downloads":
        return Path.home() / "Downloads"
    if sys.platform == "win32" and name in _CSIDL:
        try:
            import ctypes
            from ctypes import wintypes

            buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, _CSIDL[name], None, 0, buffer) == 0:
                return Path(buffer.value)
        except (AttributeError, OSError):
            pass
    fallback = {"Documents": "Documents", "Pictures": "Pictures", "Videos": "Videos", "Music": "Music",
                "Desktop": "Desktop"}.get(name)
    return Path.home() / fallback if fallback else None


def places() -> list[Place]:
    """AURORA's Documents folder first, then EXPLORER_PLACES (or the Windows libraries), existing ones only."""
    found = [Place("AURORA Documents", Path(settings.DOCUMENTS_DIR), AURORA)]
    configured = getattr(settings, "EXPLORER_PLACES", None)
    if configured is not None:
        found += [Place(name, Path(path), USER) for name, path in configured]
    else:
        for name in ("Documents", "Pictures", "Videos", "Music", "Downloads", "Desktop"):
            path = known_folder(name)
            if path is not None:
                found.append(Place(name, path, USER))
        cloud = os.environ.get("OneDrive")
        if cloud:
            found.append(Place("OneDrive", Path(cloud), CLOUD))
    seen, unique = set(), []
    for place in found:
        try:
            key = place.path.resolve()
        except OSError:
            continue
        if key in seen or (place.kind != AURORA and not place.path.is_dir()):
            continue
        seen.add(key)
        unique.append(place)
    return unique


def place_of(path: Path, available: list[Place] | None = None) -> Place | None:
    """The place containing path (the most specific one), or None if it's outside them all."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    best = None
    for place in available if available is not None else places():
        root = place.path.resolve()
        if resolved == root or resolved.is_relative_to(root):
            if best is None or len(root.parts) > len(best.path.resolve().parts):
                best = place
    return best


def is_aurora_document(path: Path) -> bool:
    """Inside AURORA's own Documents folder (so AURORA may read, index and summarise it)."""
    try:
        return Path(path).resolve().is_relative_to(Path(settings.DOCUMENTS_DIR).resolve())
    except OSError:
        return False
