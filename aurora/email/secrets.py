"""
Secrets AURORA keeps (the Gmail sign-in), encrypted with Windows' own per-user encryption (DPAPI).

Only your Windows user account on this PC can decrypt them: copying the file elsewhere, or reading it
as another user, gives nothing usable. No password of yours is ever stored, only Google's tokens.
"""

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

ENTROPY = b"AURORA secrets v1"


class SecretError(Exception):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _call(function, data: bytes) -> bytes:
    if sys.platform != "win32":
        raise SecretError("Encrypted storage needs Windows.")
    source, entropy, out = _blob(data), _blob(ENTROPY), _Blob()
    ok = function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out))  # UI_FORBIDDEN
    if not ok:
        raise SecretError("Windows couldn't encrypt or decrypt the stored sign-in.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(data: bytes) -> bytes:
    crypt = ctypes.windll.crypt32
    return _call(lambda *a: crypt.CryptProtectData(a[0], "AURORA", *a[2:]), data)


def unprotect(data: bytes) -> bytes:
    crypt = ctypes.windll.crypt32
    return _call(lambda *a: crypt.CryptUnprotectData(a[0], None, *a[2:]), data)


class SecretFile:
    """A small JSON object kept encrypted on disk. [protect]/[unprotect] are replaceable in tests."""

    def __init__(self, path: Path, protect=protect, unprotect=unprotect):
        self.path = Path(path)
        self.protect, self.unprotect = protect, unprotect

    def load(self) -> dict | None:
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.unprotect(self.path.read_bytes()).decode("utf-8"))
        except (SecretError, ValueError, UnicodeDecodeError):
            return None                                   # unreadable: treated as signed out

    def save(self, value: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_bytes(self.protect(json.dumps(value).encode("utf-8")))
        os.replace(temp, self.path)

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()
