"""
Reminders and timers.

"remind me in 10 minutes to stretch", "remind me at 5 pm to call home",
"set a timer for 3 minutes". When one is due, AURORA shows it (terminal
line, Command Centre notification).

Reminders are kept in the automation database, so closing AURORA doesn't
lose them: one still ahead is set again at the next start, and one that
fell due while AURORA was closed is reported then ("missed"), never
shown late as if it were on time. Reminder text is never logged.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from aurora.automation.store import AutomationStore
from aurora.config import settings

logger = logging.getLogger(__name__)

# Shown on time if AURORA was busy for a moment; later than this counts as missed.
LATE_TOLERANCE = timedelta(minutes=1)


@dataclass
class Reminder:
    id: int
    text: str
    due: datetime
    timer: object = None


class ReminderScheduler:

    def __init__(self, notify: Callable[[str], None] | None = None,
                 timer_factory=threading.Timer, clock: Callable[[], datetime] = datetime.now,
                 store: AutomationStore | None = None):
        self.notify = notify
        self.timer_factory = timer_factory
        self.clock = clock
        self._store = store
        self._reminders: dict[int, Reminder] = {}
        self._lock = threading.Lock()
        self.missed: list[Reminder] = []       # found by restore(); reported once
        self.listeners: list[Callable[[Reminder], None]] = []   # e.g. the event bus (Command Centre)
        self._restored = False

    @property
    def store(self) -> AutomationStore:
        if self._store is None:
            self._store = AutomationStore()
        return self._store

    def _set_status(self, reminder_id: int, status: str) -> None:
        with self.store.connect() as connection:
            connection.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))

    def _schedule(self, reminder: Reminder, delay: float) -> None:
        timer = self.timer_factory(max(delay, 0.0), self._fire, args=(reminder.id,))
        timer.daemon = True
        reminder.timer = timer
        self._reminders[reminder.id] = reminder
        timer.start()

    def restore(self) -> list[Reminder]:
        """Sets saved reminders again (once per session). Returns the ones that
        fell due while AURORA wasn't running; they are marked missed, not shown."""
        if self._restored:
            return []
        self._restored = True
        now = self.clock()
        missed = []
        with self.store.connect() as connection:
            rows = connection.execute("SELECT id, text, due FROM reminders WHERE status = 'pending' ORDER BY due").fetchall()
        for row in rows:
            reminder = Reminder(row["id"], row["text"], datetime.fromisoformat(row["due"]))
            if reminder.due + LATE_TOLERANCE < now:
                self._set_status(reminder.id, "missed")
                missed.append(reminder)
            else:
                with self._lock:
                    self._schedule(reminder, (reminder.due - now).total_seconds())
        self.missed = missed
        if rows:
            logger.info("Reminders restored: %d set again, %d missed", len(rows) - len(missed), len(missed))
        return missed

    def add(self, text: str, due: datetime) -> Reminder:
        self.restore()
        now = self.clock()
        delay = (due - now).total_seconds()
        if delay <= 0:
            raise ValueError("That time has already passed.")
        if delay > settings.REMINDER_MAX_HOURS * 3600:
            raise ValueError(f"I can only set reminders up to {settings.REMINDER_MAX_HOURS} hours ahead.")
        with self._lock:
            if len(self._reminders) >= settings.REMINDER_MAX_ACTIVE:
                raise ValueError(f"You already have {settings.REMINDER_MAX_ACTIVE} reminders set.")
            with self.store.connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO reminders (text, due, created, status) VALUES (?, ?, ?, 'pending')",
                    (text, due.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
                reminder = Reminder(cursor.lastrowid, text, due.replace(microsecond=0))
            self._schedule(reminder, delay)
        logger.info("Reminder %d set", reminder.id)
        return reminder

    def _fire(self, reminder_id: int) -> None:
        with self._lock:
            reminder = self._reminders.pop(reminder_id, None)
        if reminder is None:
            return
        self._set_status(reminder_id, "shown")
        logger.info("Reminder %d due", reminder_id)
        if self.notify:
            try:
                self.notify(f"⏰ Reminder: {reminder.text}")
            except Exception:
                logger.exception("Showing a reminder failed")
        for listener in list(self.listeners):
            try:
                listener(reminder)
            except Exception:
                logger.exception("A reminder listener failed")

    def pending(self) -> list[Reminder]:
        self.restore()
        with self._lock:
            return sorted(self._reminders.values(), key=lambda r: r.due)

    def cancel(self, reminder_id: int) -> Reminder | None:
        with self._lock:
            reminder = self._reminders.pop(reminder_id, None)
        if reminder is None:
            return None
        reminder.timer.cancel()
        self._set_status(reminder_id, "cancelled")
        return reminder

    def cancel_all(self) -> int:
        """Cancels every reminder (saved ones too)."""
        self.restore()
        with self._lock:
            reminders = list(self._reminders.values())
            self._reminders.clear()
        for reminder in reminders:
            reminder.timer.cancel()
            self._set_status(reminder.id, "cancelled")
        return len(reminders)

    def stop(self) -> None:
        """Stops the timers when AURORA closes; saved reminders stay pending."""
        with self._lock:
            reminders = list(self._reminders.values())
            self._reminders.clear()
        for reminder in reminders:
            reminder.timer.cancel()


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
