"""
Scheduled tasks: "every weekday at 18:30 turn on the desk light",
"every day at 7 run my morning briefing", "every evening at 21:00 remind
me to take my medicine", "every morning at 8 check my sensors".

The safety design (until now AURORA had no automations, because they act
without a per-change confirmation):

- **You approve each task once**, with its exact action and times spelled
  out. After that it runs at those times without asking, which is what
  makes it a schedule; pause or delete it at any time.
- **Only four kinds**, nothing else: a reminder, a routine you taught
  (routines can't contain device control, deletions or other
  confirmation-needing actions), a sensor report, or a device change, and
  only for devices you marked ``"schedulable": True`` in settings.
- **Permissions are checked at every run**, not just when the task is
  made: take away "automation" (or the device's "hardware_control") and
  the task stops running.
- **Predictable timing**: a task runs once per scheduled time, within
  ``SCHEDULE_GRACE_MINUTES`` of it. If AURORA wasn't running then, the
  run is skipped and reported, never done late (a light switched on hours
  after you meant is worse than not at all).
- **Every run is reported** (a notification / terminal line), written to
  the audit trail, and device changes can be undone ("undo that").
- **Error recovery**: a task that fails ``SCHEDULE_MAX_FAILURES`` (3)
  times in a row is paused and you are told.
"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from aurora.automation.store import AutomationStore
from aurora.config import settings

logger = logging.getLogger(__name__)

KINDS = ("reminder", "routine", "device", "sensors")
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_SETS = {"daily": set(range(7)), "weekdays": set(range(5)), "weekends": {5, 6}}
FULL_DAY = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
            "sat": "Saturday", "sun": "Sunday"}


def day_set(days: str) -> set[int]:
    if days in DAY_SETS:
        return DAY_SETS[days]
    chosen = {DAY_NAMES.index(day) for day in days.split(",") if day in DAY_NAMES}
    if not chosen:
        raise ValueError(f"unknown days {days!r}")
    return chosen


def describe_days(days: str) -> str:
    if days == "daily":
        return "every day"
    if days in ("weekdays", "weekends"):
        return f"every {days[:-1]}"
    names = [FULL_DAY[day] for day in days.split(",")]
    return "every " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1])


@dataclass
class Task:
    id: int
    name: str                 # what it does, in words ("turn on the desk light")
    kind: str
    target: dict
    days: str
    at: str                   # HH:MM
    created: datetime
    active: bool = True
    last_run: datetime | None = None
    last_result: str = ""
    last_success: bool | None = None
    failures: int = 0

    def when(self) -> str:
        return f"{describe_days(self.days)} at {self.at}"

    def occurrence_on(self, day: datetime) -> datetime:
        hour, minute = (int(part) for part in self.at.split(":"))
        return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def latest_occurrence(self, now: datetime) -> datetime | None:
        """The most recent scheduled time at or before ``now`` (looking back a week)."""
        for back in range(8):
            moment = self.occurrence_on(now - timedelta(days=back))
            if moment <= now and moment.weekday() in day_set(self.days):
                return moment
        return None

    def next_run(self, now: datetime) -> datetime | None:
        if not self.active:
            return None
        for ahead in range(8):
            moment = self.occurrence_on(now + timedelta(days=ahead))
            if moment > now and moment.weekday() in day_set(self.days):
                return moment
        return None

    def line(self, now: datetime | None = None) -> str:
        now = now or datetime.now()
        state = "" if self.active else " (paused)"
        upcoming = self.next_run(now)
        parts = [f"{self.id}. {self.when()}: {self.name}{state}"]
        if upcoming:
            day = "today" if upcoming.date() == now.date() else (
                "tomorrow" if upcoming.date() == (now + timedelta(days=1)).date() else f"{upcoming:%a %d %b}")
            parts.append(f"next {day} {upcoming:%H:%M}")
        if self.last_run:
            outcome = "done" if self.last_success else ("skipped" if self.last_success is None else "failed")
            parts.append(f"last {self.last_run:%d %b %H:%M} {outcome}")
        return "; ".join(parts)


class TaskStore:

    def __init__(self, store: AutomationStore | None = None):
        self._store = store

    @property
    def store(self) -> AutomationStore:
        if self._store is None:
            self._store = AutomationStore()
        return self._store

    @staticmethod
    def _task(row) -> Task:
        def when(value):
            return datetime.fromisoformat(value) if value else None
        success = row["last_success"]
        return Task(row["id"], row["name"], row["kind"], json.loads(row["target"]), row["days"], row["at"],
                    datetime.fromisoformat(row["created"]), bool(row["active"]), when(row["last_run"]),
                    row["last_result"] or "", None if success is None else bool(success), row["failures"])

    def add(self, name: str, kind: str, target: dict, days: str, at: str, now: datetime | None = None) -> Task:
        if kind not in KINDS:
            raise ValueError(f"unknown task kind {kind!r}")
        day_set(days)
        hour, minute = (int(part) for part in at.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"bad time {at!r}")
        created = (now or datetime.now()).isoformat(timespec="seconds")
        with self.store.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks (name, kind, target, days, at, created) VALUES (?, ?, ?, ?, ?, ?)",
                (name, kind, json.dumps(target), days, f"{hour:02d}:{minute:02d}", created))
            task_id = cursor.lastrowid
        return self.get(task_id)

    def get(self, task_id: int) -> Task | None:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task(row) if row else None

    def all(self) -> list[Task]:
        with self.store.connect() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [self._task(row) for row in rows]

    def set_active(self, task_id: int, active: bool) -> bool:
        with self.store.connect() as connection:
            changed = connection.execute("UPDATE tasks SET active = ?, failures = 0 WHERE id = ?",
                                         (int(active), task_id)).rowcount
        return bool(changed)

    def delete(self, task_id: int) -> bool:
        with self.store.connect() as connection:
            return bool(connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount)

    def record(self, task: Task, moment: datetime, success: bool | None, message: str, failures: int,
               active: bool) -> None:
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE tasks SET last_run = ?, last_result = ?, last_success = ?, failures = ?, active = ? WHERE id = ?",
                (moment.isoformat(timespec="seconds"), message[:500], None if success is None else int(success),
                 failures, int(active), task.id))


@dataclass
class Run:
    task: Task
    moment: datetime          # the scheduled time this run was for
    success: bool | None      # None: skipped
    message: str
    paused: bool = False


class Scheduler:
    """Finds due tasks and runs them through ``runner`` (the engine)."""

    def __init__(self, tasks: TaskStore, runner: Callable[[Task], "object"],
                 on_run: Callable[[Run], None] | None = None, clock: Callable[[], datetime] = datetime.now):
        self.tasks = tasks
        self.runner = runner
        self.on_run = on_run
        self.clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> list[Run]:
        """Runs (or skips) every task whose time has come since it last ran."""
        now = self.clock()
        grace = timedelta(minutes=settings.SCHEDULE_GRACE_MINUTES)
        runs = []
        for task in self.tasks.all():
            if not task.active:
                continue
            moment = task.latest_occurrence(now)
            if moment is None or moment < task.created or (task.last_run and moment <= task.last_run):
                continue
            if now - moment > grace:
                message = f"Skipped the {moment:%H:%M} run: AURORA wasn't running then."
                self.tasks.record(task, moment, None, message, task.failures, True)
                runs.append(Run(task, moment, None, message))
                continue
            try:
                result = self.runner(task)
                success, message = bool(result.success), result.message
            except Exception as error:          # a task must never stop the scheduler
                logger.exception("Scheduled task %d crashed", task.id)
                success, message = False, f"It failed unexpectedly ({type(error).__name__})."
            failures = 0 if success else task.failures + 1
            paused = failures >= settings.SCHEDULE_MAX_FAILURES
            if paused:
                message += f" Paused after {failures} failures in a row; say \"resume task {task.id}\" to try again."
            self.tasks.record(task, moment, success, message, failures, not paused)
            runs.append(Run(task, moment, success, message, paused))
            logger.info("Scheduled task %d ran: success=%s", task.id, success)
        for run in runs:
            if self.on_run:
                try:
                    self.on_run(run)
                except Exception:
                    logger.exception("Reporting a scheduled task failed")
        return runs

    # --- Background loop ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="aurora-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.check()
            except Exception:
                logger.exception("Checking scheduled tasks failed")
            if self._stop.wait(settings.SCHEDULE_CHECK_SECONDS):
                break
