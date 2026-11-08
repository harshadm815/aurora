"""
The Automation service: reminders and scheduled tasks for the whole
session, whichever front end (terminal or Command Centre) is running.

- At start: saved reminders are set again; ones that fell due while
  AURORA was closed are reported (``automation.reminders_missed``).
- Every due reminder is published (``automation.reminder_due``).
- The scheduler checks for due tasks every ``SCHEDULE_CHECK_SECONDS``
  and runs them through the engine; each run (or skip) is published
  (``automation.task_ran``) and written to the audit trail.
- At shutdown: timers stop; saved reminders and tasks stay for next time.
"""

import logging

from aurora.automation.schedule import Run, Scheduler
from aurora.events import topics
from aurora.kernel.lifecycle import ServiceState
from aurora.kernel.service import BaseService

logger = logging.getLogger(__name__)

SOURCE = "Automation"


class AutomationService(BaseService):

    def __init__(self, engine, reminders=None, schedule=None):
        super().__init__("Automation")
        self.engine = engine
        self.reminders = reminders          # RemindersTool or None
        self.schedule = schedule            # ScheduleTool or None
        self.scheduler: Scheduler | None = None
        if schedule is not None:
            self.scheduler = Scheduler(schedule.tasks, engine.run_task, on_run=self._report)
        if reminders is not None:
            reminders.scheduler.listeners.append(self._reminder_due)

    def start(self):
        if self.reminders is not None:
            missed = self.reminders.scheduler.restore()
            if missed:
                self.engine.events.emit(topics.REMINDERS_MISSED, {
                    "reminders": [{"text": r.text, "due": r.due.isoformat(timespec="minutes")} for r in missed]},
                    source=SOURCE)
        if self.scheduler is not None:
            self.scheduler.start()
        self.state = ServiceState.RUNNING

    def stop(self):
        if self.scheduler is not None:
            self.scheduler.stop()
        if self.reminders is not None:
            self.reminders.scheduler.stop()
        self.state = ServiceState.STOPPED

    def _reminder_due(self, reminder) -> None:
        self.engine.events.emit(topics.REMINDER_DUE, {"id": reminder.id, "text": reminder.text}, source=SOURCE)

    def _report(self, run: Run) -> None:
        if run.success is None:
            self.engine.executor.audit.record("schedule.run", "skipped", f"{run.task.name}: {run.message}",
                                              f"schedule #{run.task.id}")
        self.engine.events.emit(topics.TASK_RAN, {
            "id": run.task.id, "name": run.task.name, "when": run.task.when(),
            "scheduled_for": run.moment.isoformat(timespec="minutes"),
            "success": run.success, "message": run.message, "paused": run.paused}, source=SOURCE)
