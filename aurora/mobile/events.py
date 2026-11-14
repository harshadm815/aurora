"""
What the phone hears about without asking: the event feed.

The phone keeps one request open, ``GET /api/events?after=N`` (long
polling): the server answers as soon as something new happens, or after
``wait`` seconds with nothing. No extra port, no extra protocol: it goes
through the same Host check, device key and signature as everything else.

Events (kept in memory only, the last EVENT_KEEP of them):
    {"id", "kind": "message", "source": "pc" | "phone", "text", "reply", "time"}
    {"id", "kind": "reminder", "text", "time"}
    {"id", "kind": "call", "reason", "time"}           AURORA rings the phone
    {"id", "kind": "file", "file", "name", "size"}     a file sent from the PC (fetch it with /api/file?id=)

``session`` changes each time AURORA starts, so the phone knows that ids
started again from 1. ``after=-1`` asks only for the current position
(a phone that just connected doesn't get old events as notifications).
"""

import secrets
import threading
from collections import deque
from datetime import datetime

EVENT_KEEP = 200
MAX_WAIT_SECONDS = 30


class EventHub:

    def __init__(self, keep: int = EVENT_KEEP):
        self.session = secrets.token_hex(4)
        self._events: deque[dict] = deque(maxlen=keep)
        self._last = 0
        self._closed = False
        self._condition = threading.Condition()

    @property
    def last(self) -> int:
        return self._last

    def publish(self, kind: str, **data) -> dict:
        with self._condition:
            self._last += 1
            event = {"id": self._last, "kind": kind, "time": datetime.now().isoformat(timespec="seconds"), **data}
            self._events.append(event)
            self._condition.notify_all()
        return event

    def since(self, after: int, wait: float = 0, session: str | None = None) -> dict:
        """Events newer than ``after``, waiting up to ``wait`` seconds for the first one. A ``session``
        from before a restart means the phone's ``after`` belongs to the old run: everything is new."""
        wait = max(0.0, min(float(wait), MAX_WAIT_SECONDS))
        with self._condition:
            if after < 0:
                return {"session": self.session, "last": self._last, "events": []}
            if after > self._last or (session and session != self.session):
                after = 0
            self._condition.wait_for(lambda: self._closed or self._last > after, timeout=wait)
            events = [e for e in self._events if e["id"] > after]
            return {"session": self.session, "last": self._last, "events": events}

    def close(self) -> None:
        """Wakes every waiting request (the server is stopping)."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
