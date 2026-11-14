"""
Which network this PC is on, its address there, and helping the phone find it.

AURORA listens on Wi-Fi only on networks you marked as home (Settings →
Phone). The network is identified by its Wi-Fi name (SSID), read from
Windows (``netsh wlan show interfaces``). The phone first tries the address
it last used; if that fails it sends a small broadcast ("AURORA?") on UDP
port MOBILE_DISCOVERY_PORT and the PC answers with its HTTPS port. The
answer proves nothing by itself: the phone still accepts only the PC's
pinned certificate and the PC only the paired phone's signatures.
"""

import json
import logging
import re
import socket
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

DISCOVERY_QUESTION = b"AURORA?"
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0         # CREATE_NO_WINDOW


def run_quietly(command: list[str], timeout: float = 10) -> str:
    """Runs a small command without a console window; its output, or "" if it failed."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                              creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def parse_ssid(netsh_output: str) -> str | None:
    """The connected Wi-Fi name from ``netsh wlan show interfaces`` (not the BSSID line)."""
    if not re.search(r"^\s*State\s*:\s*connected\s*$", netsh_output, re.MULTILINE | re.IGNORECASE):
        return None
    match = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", netsh_output, re.MULTILINE)
    return match.group(1) if match else None


def current_ssid(run=run_quietly) -> str | None:
    """The Wi-Fi network this PC is connected to, or None (cable, no Wi-Fi, not Windows)."""
    if sys.platform != "win32" and run is run_quietly:
        return None
    return parse_ssid(run(["netsh", "wlan", "show", "interfaces"]))


def lan_address() -> str | None:
    """This PC's address on its main network. A UDP "connect" sends nothing; it only picks the route."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))               # TEST-NET-1: never reached, nothing is sent
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith(("127.", "0.")) else address


class DiscoveryResponder:
    """Answers "AURORA?" broadcasts on the home network with the HTTPS port and a fingerprint prefix."""

    def __init__(self, address: str, port: int, https_port: int, fingerprint: str):
        self.answer = json.dumps({"aurora": 1, "port": https_port, "fp": fingerprint[:16]}).encode("ascii")
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((address, port))
        self._socket.settimeout(1.0)
        self._running = True
        self._thread = threading.Thread(target=self._serve, name="aurora-discovery", daemon=True)
        self._thread.start()

    def _serve(self):
        while self._running:
            try:
                data, sender = self._socket.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.strip() == DISCOVERY_QUESTION:
                try:
                    self._socket.sendto(self.answer, sender)
                except OSError:
                    pass

    def stop(self):
        self._running = False
        self._socket.close()
        self._thread.join(timeout=2)
