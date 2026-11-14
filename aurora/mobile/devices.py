"""
Paired phones and the codes that pair them.

Pairing: the Command Centre shows a one-time code (8 characters, valid
10 minutes, one use). The AURORA app sends it, over the USB cable only,
with a name and the public half of a signing key it made in the phone's
Keystore; AURORA replies with a long random device key, which only the
phone keeps. AURORA stores just a SHA-256 hash of the device key (so the
database alone can't be used to connect) and the phone's public key
(signing.py: every request must be signed by that phone). Five wrong
codes and the current code is withdrawn.

Only one phone (settings.MOBILE_MAX_PHONES) can be paired: while it is,
no code can be made and pairing is refused. Disconnect it to pair another.

Each device has permissions (chat, reminders and briefing always; seeing
memories and approving actions only if you allow them) and a last-seen
time. The access log records which device used which part of the API and
whether it was allowed, never the messages themselves.
"""

import hashlib
import hmac
import logging
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from aurora.config import settings
from aurora.mobile.signing import SignatureError, load_public_key

logger = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O, 1/I: easy to type on a phone
CODE_LENGTH = 8
CODE_MINUTES = 10
MAX_WRONG_CODES = 5
LOG_KEEP_DAYS = 30


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Device:
    id: int
    name: str
    created: datetime
    last_seen: datetime | None
    allow_memories: bool
    allow_actions: bool
    public_key: str = ""


class PairingError(Exception):
    pass


class DeviceStore:

    def __init__(self, path: str | Path | None = None, clock=datetime.now):
        self.path = Path(path or settings.MOBILE_DATABASE_PATH)
        self.clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._code_expires = datetime.min
        self._wrong = 0
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS devices (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL,
                            token_hash TEXT NOT NULL UNIQUE,
                            created TEXT NOT NULL,
                            last_seen TEXT,
                            allow_memories INTEGER NOT NULL DEFAULT 0,
                            allow_actions INTEGER NOT NULL DEFAULT 0)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(devices)")}
            if "public_key" not in columns:
                db.execute("ALTER TABLE devices ADD COLUMN public_key TEXT NOT NULL DEFAULT ''")
            # Phones paired before signing keys (the 0.45 web app) can't prove who they are: unpair them.
            removed = db.execute("DELETE FROM devices WHERE public_key = ''").rowcount
            if removed:
                logger.info("Mobile: %d phone(s) without a signing key were disconnected", removed)
            db.execute("""CREATE TABLE IF NOT EXISTS access_log (
                            time TEXT NOT NULL, device TEXT NOT NULL, what TEXT NOT NULL, outcome TEXT NOT NULL)""")

    def _connect(self):
        return sqlite3.connect(self.path)

    # ---------------------------------------------------------------- pairing

    @property
    def full(self) -> bool:
        """True while the allowed number of phones (one) is paired."""
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] >= settings.MOBILE_MAX_PHONES

    def new_code(self) -> str:
        """A fresh one-time pairing code (any earlier one stops working)."""
        if self.full:
            raise PairingError("Your phone is already paired. Disconnect it in Settings → Phone to pair another.")
        with self._lock:
            self._code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            self._code_expires = self.clock() + timedelta(minutes=CODE_MINUTES)
            self._wrong = 0
        logger.info("Mobile: pairing code created")
        return self._code

    @property
    def code_active(self) -> bool:
        return self._code is not None and self.clock() < self._code_expires

    def cancel_code(self) -> None:
        with self._lock:
            self._code = None

    def pair(self, code: str, name: str, public_key: str) -> tuple[str, Device]:
        """Exchanges a valid code (and the phone's public key) for a device key. Raises PairingError otherwise."""
        name = " ".join((name or "").split())[:40] or "Phone"
        typed = "".join(ch for ch in (code or "").upper() if ch.isalnum())
        try:
            load_public_key(public_key or "")
        except SignatureError as error:
            raise PairingError(str(error))
        if self.full:
            with self._lock:
                self._code = None
            raise PairingError("A phone is already paired. Disconnect it on the PC to pair another.")
        with self._lock:
            if self._code is None or self.clock() >= self._code_expires:
                self._code = None
                raise PairingError("There's no pairing code waiting. Make a new one on the PC.")
            if not hmac.compare_digest(typed, self._code):
                self._wrong += 1
                if self._wrong >= MAX_WRONG_CODES:
                    self._code = None
                    logger.warning("Mobile: pairing code withdrawn after %d wrong attempts", self._wrong)
                    raise PairingError("Too many wrong codes. Make a new one on the PC.")
                raise PairingError("That code isn't right.")
            self._code = None                     # one use only
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self._connect() as db:
            cursor = db.execute("INSERT INTO devices (name, token_hash, created, public_key) VALUES (?, ?, ?, ?)",
                                (name, _hash(token), now.isoformat(), public_key))
            device_id = cursor.lastrowid
        logger.info("Mobile: a device was paired")
        self.log(name, "pair", "allowed")
        return token, Device(device_id, name, now, None, False, False, public_key)

    # ---------------------------------------------------------------- devices

    def _row(self, row) -> Device:
        return Device(row[0], row[1], datetime.fromisoformat(row[2]),
                      datetime.fromisoformat(row[3]) if row[3] else None, bool(row[4]), bool(row[5]), row[6])

    def devices(self) -> list[Device]:
        with self._connect() as db:
            rows = db.execute("SELECT id, name, created, last_seen, allow_memories, allow_actions, public_key FROM devices "
                              "ORDER BY id").fetchall()
        return [self._row(r) for r in rows]

    def authenticate(self, token: str | None) -> Device | None:
        """The device this key belongs to (and marks it seen), or None. The caller must also check
        the request's signature against ``device.public_key`` (signing.verify)."""
        if not token or len(token) > 200:
            return None
        with self._connect() as db:
            row = db.execute("SELECT id, name, created, last_seen, allow_memories, allow_actions, public_key FROM devices "
                             "WHERE token_hash = ?", (_hash(token),)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (self.clock().isoformat(), row[0]))
        return self._row(row)

    def set_permissions(self, device_id: int, memories: bool | None = None, actions: bool | None = None) -> None:
        with self._connect() as db:
            if memories is not None:
                db.execute("UPDATE devices SET allow_memories = ? WHERE id = ?", (int(memories), device_id))
            if actions is not None:
                db.execute("UPDATE devices SET allow_actions = ? WHERE id = ?", (int(actions), device_id))

    def remove(self, device_id: int) -> None:
        """Disconnects a device: its key stops working at once."""
        with self._connect() as db:
            db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        logger.info("Mobile: a device was disconnected")

    # ---------------------------------------------------------------- log

    def log(self, device: str, what: str, outcome: str) -> None:
        now = self.clock()
        with self._connect() as db:
            db.execute("INSERT INTO access_log VALUES (?, ?, ?, ?)", (now.isoformat(), device, what, outcome))
            db.execute("DELETE FROM access_log WHERE time < ?", ((now - timedelta(days=LOG_KEEP_DAYS)).isoformat(),))

    def recent_log(self, limit: int = 50) -> list[tuple[datetime, str, str, str]]:
        with self._connect() as db:
            rows = db.execute("SELECT time, device, what, outcome FROM access_log ORDER BY time DESC LIMIT ?",
                              (limit,)).fetchall()
        return [(datetime.fromisoformat(t), d, w, o) for t, d, w, o in rows]
