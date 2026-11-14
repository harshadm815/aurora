"""
Files between the phone and the PC.

**Phone → PC** (``POST /api/file?name=N``, the phone's share menu): saved in
MOBILE_FILES_DIR ("Documents\\From phone"), never over an existing file
("photo.jpg", then "photo (2).jpg", ...). The phone signs the file's SHA-256
up front (``X-Aurora-Content-Sha256``, part of the signature), so the file
is checked as it arrives and anything that doesn't match is thrown away.
Nothing is opened or run; on Windows each file is marked as coming from
another device (the "Mark of the Web"), as browser downloads are.

**PC → phone** (Settings → Phone → "Send a file to my phone…"): the file is
offered for an hour under a random id (an event tells the phone), and the
phone fetches it with ``GET /api/file?id=I`` into its Downloads\\AURORA folder.
Only files you chose are offered; the phone can't ask for any other path.
"""

import hashlib
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

CHUNK = 256 * 1024
OUTBOX_SECONDS = 3600
SPARE_BYTES = 500 * 1024 * 1024            # never fill the disk: keep this much free
MAX_NAME = 120
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_BAD = re.compile(r'[\x00-\x1f<>:"/\\|?*\x7f]')


class FileError(Exception):
    pass


def safe_name(name: str) -> str:
    """A plain file name that's safe on Windows: no folders, no reserved or invisible characters."""
    name = re.split(r"[\\/]", str(name or ""))[-1]
    name = " ".join(_BAD.sub(" ", name).split()).strip(" .")
    if not name or name.startswith("."):
        name = f"shared file{name}" if name else "shared file"
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem or len(extension) > 12:
        stem, extension = name, ""
    if stem.split(".")[0].upper() in _RESERVED:
        stem = f"_{stem}"
    stem = stem[:MAX_NAME - len(extension) - 1].rstrip(" .") or "shared file"
    return f"{stem}.{extension}" if extension else stem


def _reserve(folder: Path, name: str) -> Path:
    """Creates an empty file with ``name`` or the first free "name (n)", so nothing is overwritten."""
    stem, extension = os.path.splitext(name)
    for n in range(1, 10_000):
        path = folder / (name if n == 1 else f"{stem} ({n}){extension}")
        try:
            with open(path, "xb"):
                return path
        except FileExistsError:
            continue
    raise FileError("There are too many files with that name already.")


def _mark_from_another_device(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as zone:
            zone.write("[ZoneTransfer]\r\nZoneId=3\r\n")
    except OSError:
        pass


def receive(stream, length: int, folder: Path, name: str, expected_sha256: str, limit: int) -> Path:
    """Saves ``length`` bytes from ``stream`` as ``name`` in ``folder``; returns where. Raises FileError."""
    if length <= 0:
        raise FileError("The file is empty.")
    if length > limit:
        raise FileError(f"That file is too big (the limit is {limit // (1024 * 1024)} MB).")
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < length + SPARE_BYTES:
        raise FileError("There isn't enough free space on the PC for that file.")
    partial = folder / f".incoming-{secrets.token_hex(6)}.part"
    digest = hashlib.sha256()
    try:
        with open(partial, "xb") as out:
            left = length
            while left:
                chunk = stream.read(min(CHUNK, left))
                if not chunk:
                    raise FileError("The file didn't arrive completely. Try again.")
                digest.update(chunk)
                out.write(chunk)
                left -= len(chunk)
        if not secrets.compare_digest(digest.hexdigest(), (expected_sha256 or "").lower()):
            raise FileError("The file arrived damaged, so it wasn't kept. Try again.")
        final = _reserve(folder, safe_name(name))
        os.replace(partial, final)                    # over our own empty placeholder only
    finally:
        if partial.exists():
            partial.unlink()
    _mark_from_another_device(final)
    return final


@dataclass
class Offer:
    path: Path
    name: str
    size: int
    expires: float


class Outbox:
    """Files you chose to send to the phone, each fetchable for an hour by its random id."""

    def __init__(self, limit: int, clock=time.time, keep_seconds: float = OUTBOX_SECONDS):
        self.limit = limit
        self.clock = clock
        self.keep_seconds = keep_seconds
        self._offers: dict[str, Offer] = {}
        self._lock = threading.Lock()

    def offer(self, path) -> tuple[str, Offer]:
        path = Path(path)
        if not path.is_file():
            raise FileError("That file isn't there any more.")
        size = path.stat().st_size
        if size <= 0:
            raise FileError("That file is empty.")
        if size > self.limit:
            raise FileError(f"That file is too big (the limit is {self.limit // (1024 * 1024)} MB).")
        offer = Offer(path, safe_name(path.name), size, self.clock() + self.keep_seconds)
        key = secrets.token_urlsafe(16)
        with self._lock:
            self._tidy()
            self._offers[key] = offer
        return key, offer

    def get(self, key: str) -> Offer | None:
        with self._lock:
            self._tidy()
            offer = self._offers.get(str(key or ""))
        if offer is None or not offer.path.is_file() or offer.path.stat().st_size != offer.size:
            return None                                 # gone or changed since you sent it
        return offer

    def _tidy(self) -> None:
        now = self.clock()
        self._offers = {k: o for k, o in self._offers.items() if o.expires > now}
