"""
Reminders and timers for the current session.

"remind me in 10 minutes to stretch", "remind me at 5 pm to call home",
"set a timer for 3 minutes". When one is due, AURORA shows it in the
terminal. (It isn't spoken: reminders fire on a background thread, and
the Windows speech engine must only be used from the main thread.)

They are kept in memory only: closing AURORA cancels them, and AURORA
says so when you set one. Reminder text is never logged.
"""

import itertools
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from aurora.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Reminder:
    id: int
    text: str
    due: datetime
    timer: object = None


class ReminderScheduler:

    def __init__(self, notify: Callable[[str], None] | None = None,
                 timer_factory=threading.Timer, clock: Callable[[], datetime] = datetime.now):
        self.notify = notify
        self.timer_factory = timer_factory
        self.clock = clock
        self._ids = itertools.count(1)
        self._reminders: dict[int, Reminder] = {}
        self._lock = threading.Lock()

    def add(self, text: str, due: datetime) -> Reminder:
        now = self.clock()
        delay = (due - now).total_seconds()
        if delay <= 0:
            raise ValueError("That time has already passed.")
        if delay > settings.REMINDER_MAX_HOURS * 3600:
            raise ValueError(f"I can only set reminders up to {settings.REMINDER_MAX_HOURS} hours ahead.")
        with self._lock:
            if len(self._reminders) >= settings.REMINDER_MAX_ACTIVE:
                raise ValueError(f"You already have {settings.REMINDER_MAX_ACTIVE} reminders set.")
            reminder = Reminder(next(self._ids), text, due)
            timer = self.timer_factory(delay, self._fire, args=(reminder.id,))
            timer.daemon = True
            reminder.timer = timer
            self._reminders[reminder.id] = reminder
        timer.start()
        logger.info("Reminder %d set", reminder.id)
        return reminder

    def _fire(self, reminder_id: int) -> None:
        with self._lock:
            reminder = self._reminders.pop(reminder_id, None)
        if reminder is None:
            return
        logger.info("Reminder %d due", reminder_id)
        if self.notify:
            try:
                self.notify(f"⏰ Reminder: {reminder.text}")
            except Exception:
                logger.exception("Showing a reminder failed")

    def pending(self) -> list[Reminder]:
        with self._lock:
            return sorted(self._reminders.values(), key=lambda r: r.due)

    def cancel_all(self) -> int:
        with self._lock:
            reminders = list(self._reminders.values())
            self._reminders.clear()
        for reminder in reminders:
            reminder.timer.cancel()
        return len(reminders)


def due_time(now: datetime, seconds: int | None = None, at: str | None = None) -> datetime:
    """'in N seconds' or 'at HH:MM' (today, or tomorrow if that time has passed)."""
    if seconds is not None:
        return now + timedelta(seconds=seconds)
    hour, minute = (int(part) for part in at.split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return due if due > now else due + timedelta(days=1)


def in_words(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(minutes, 60)
    text = f"{hours} hour{'s' if hours != 1 else ''}"
    return text + (f" {rest} minute{'s' if rest != 1 else ''}" if rest else "")
