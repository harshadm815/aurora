"""
Away from home: the phone reaches AURORA through Tailscale.

Tailscale (tailscale.com, free for personal use) joins your PC and phone in
a private network of your own, encrypted end to end (WireGuard). It isn't
bundled with AURORA: install it on both and sign in to the same account.

``tailscale serve --bg PORT`` then gives the PC an HTTPS address that only
your own Tailscale devices can open (``https://<pc>.<tailnet>.ts.net``),
passed to AURORA's local listener. AURORA never uses ``tailscale funnel``
(which would publish it to the whole internet).

The phone learns that address from ``/api/connection`` and tries it after
the cable and home Wi-Fi. On that route Tailscale's own certificate is
checked as usual (it's a real one) and the host must end in ``.ts.net``;
every request is still signed by the phone's key, and pairing stays
USB-only (the Host is the ts.net name, not localhost).
"""

import json
import logging
import os
import shutil
import threading
from pathlib import Path

from aurora.mobile.network import run_quietly

logger = logging.getLogger(__name__)


def find_tailscale() -> str | None:
    for folder in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if folder and (Path(folder) / "Tailscale" / "tailscale.exe").is_file():
            return str(Path(folder) / "Tailscale" / "tailscale.exe")
    return shutil.which("tailscale")


def parse_status(text: str) -> str | None:
    """This PC's Tailscale name (``pc.tailnet.ts.net``) from ``tailscale status --json``, if connected."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return None
    me = data.get("Self") or {}
    if not isinstance(me, dict) or data.get("BackendState") != "Running":
        return None
    name = str(me.get("DNSName") or "").rstrip(".").lower()
    return name if name.endswith(".ts.net") else None


def parse_serve(text: str, port: int) -> bool:
    """True if ``tailscale serve status --json`` shows HTTPS passed to 127.0.0.1:``port`` (and no funnel)."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    targets = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", f"127.0.0.1:{port}", f"localhost:{port}"}
    for site in (data.get("Web") or {}).values():
        for handler in ((site or {}).get("Handlers") or {}).values():
            if str((handler or {}).get("Proxy", "")).rstrip("/") in targets:
                return True
    return False


class Tailscale:
    """What Tailscale on this PC offers the phone. ``run`` is replaced in tests."""

    def __init__(self, port: int, executable: str | None = None, run=run_quietly):
        self.port = port
        self.executable = executable
        self.run = run
        self.name: str | None = None           # the PC's ts.net name while Tailscale is connected
        self.serving = False                   # ``tailscale serve`` passes it to AURORA
        self._lock = threading.Lock()

    @property
    def installed(self) -> bool:
        return self.executable is not None

    @property
    def address(self) -> str | None:
        """What the phone is told: the HTTPS address, only when it actually leads to AURORA."""
        return self.name if self.name and self.serving else None

    def check(self) -> None:
        if self.executable is None:
            return
        with self._lock:
            self.name = parse_status(self.run([self.executable, "status", "--json"]))
            self.serving = self.name is not None and parse_serve(
                self.run([self.executable, "serve", "status", "--json"]), self.port)

    def serve(self) -> str:
        """Sets up ``tailscale serve`` for AURORA (you pressed the button). Returns "" or what went wrong."""
        if self.executable is None:
            return "Tailscale isn't installed on this PC."
        self.check()
        if self.name is None:
            return "Tailscale isn't connected. Open Tailscale on the PC and sign in."
        if not self.serving:
            self.run([self.executable, "serve", "--bg", str(self.port)], timeout=20)
            self.check()
        if not self.serving:
            return "Tailscale didn't accept the setting (it may need HTTPS turned on for your tailnet in its admin page)."
        logger.info("Mobile: Tailscale serve set up")
        return ""
