"""
The USB cable link.

While the phone server is on, AURORA checks every few seconds which
Android phones are plugged in with USB debugging allowed (``adb devices``)
and, for each new one, runs ``adb reverse tcp:PORT tcp:PORT``: the phone's
own ``localhost:PORT`` then leads to AURORA on this PC, through the cable
only. Pairing is accepted only this way.

adb comes from Google's Android Platform Tools (not bundled with AURORA):
settings.MOBILE_ADB_PATH, else the Android SDK folders, else PATH.
"""

import logging
import os
import shutil
import threading
from pathlib import Path

from aurora.config import settings
from aurora.mobile.network import run_quietly

logger = logging.getLogger(__name__)

POLL_SECONDS = 5


def find_adb() -> str | None:
    candidates = [settings.MOBILE_ADB_PATH] if settings.MOBILE_ADB_PATH else []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "platform-tools" / "adb.exe")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates += [Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
                       Path(local) / "Android" / "platform-tools" / "adb.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return shutil.which("adb")


def parse_devices(output: str) -> list[str]:
    """Serial numbers of phones ready for adb (not "unauthorized" or "offline")."""
    serials = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


class UsbLink:
    """Keeps ``adb reverse`` set up for every plugged-in phone, in a background thread."""

    def __init__(self, port: int, adb: str | None = None, run=run_quietly, poll: float = POLL_SECONDS):
        self.port = port
        self.adb = adb
        self.run = run
        self.poll = poll
        self.phones: list[str] = []            # serials currently linked
        self._linked: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return self.adb is not None

    def check(self) -> list[str]:
        """One round: link new phones, forget unplugged ones. Returns the linked serials."""
        if self.adb is None:
            return []
        present = parse_devices(self.run([self.adb, "devices"]))
        for serial in present:
            if serial not in self._linked:
                rule = f"tcp:{self.port}"
                if self.run([self.adb, "-s", serial, "reverse", rule, rule]) != "" or self._reversed(serial):
                    self._linked.add(serial)
                    logger.info("Mobile: USB link to a phone set up")
        self._linked &= set(present)
        self.phones = sorted(self._linked)
        return self.phones

    def _reversed(self, serial: str) -> bool:
        return f"tcp:{self.port}" in self.run([self.adb, "-s", serial, "reverse", "--list"])

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:
                logger.exception("Mobile: USB check failed")
            self._stop.wait(self.poll)

    def start(self):
        if self.adb is None or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="aurora-usb", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
