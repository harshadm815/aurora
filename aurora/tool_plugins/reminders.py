from aurora.automation.reminders import ReminderScheduler, due_time, in_words
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.tools.untrusted import one_line

KEPT_NOTE = "If AURORA is closed when it's due, I'll tell you the next time it starts."


class RemindersTool(BaseTool):
    """Set, list and cancel reminders and timers (kept on disk)."""

    def __init__(self, scheduler: ReminderScheduler | None = None):
        super().__init__("Reminders")
        self.scheduler = scheduler or ReminderScheduler()

    def execute(self, action: str = "add", text: str = "", seconds: int | None = None,
                at: str | None = None, timer: bool = False, which: str = "", **_) -> ToolResult:
        if action == "list":
            pending = self.scheduler.pending()
            if not pending:
                return ToolResult(True, "You have no reminders set.", [])
            lines = [f"  {r.id}. {r.due:%H:%M} {r.text}" for r in pending]
            return ToolResult(True, "Your reminders:\n" + "\n".join(lines), pending)

        if action == "cancel":
            if which:
                return self.cancel_one(which)
            count = self.scheduler.cancel_all()
            return ToolResult(True, f"Cancelled {count} reminder{'s' if count != 1 else ''}.", count)

        if seconds is None and at is None:
            return ToolResult(False, "When should I remind you? For example: \"in 10 minutes\" or \"at 5 pm\".")

        now = self.scheduler.clock()
        due = due_time(now, seconds, at)
        wait = in_words((due - now).total_seconds())
        text = one_line(text) or (f"Your timer for {wait} is up." if timer else "Time's up.")

        try:
            reminder = self.scheduler.add(text, due)
        except ValueError as error:
            return ToolResult(False, str(error))

        if timer:
            message = f"Timer set for {wait} (until {due:%H:%M})."
        else:
            ending = "" if text[-1:] in ".!?" else "."
            message = f"Okay, I'll remind you at {due:%H:%M} (in {wait}): {text}{ending}"
        result = ToolResult(True, f"{message} {KEPT_NOTE}", reminder)
        result.undo = lambda: self._undo(reminder.id)
        result.undo_label = f"cancel the reminder for {due:%H:%M}"
        return result

    def _undo(self, reminder_id: int) -> ToolResult:
        if self.scheduler.cancel(reminder_id) is None:
            return ToolResult(False, "That reminder has already been shown or cancelled.")
        return ToolResult(True, "Reminder cancelled.")

    def cancel_one(self, which: str) -> ToolResult:
        """"cancel reminder 2" or "cancel the reminder about stretching"."""
        pending = self.scheduler.pending()
        wanted = which.strip().lower()
        if wanted.isdigit():
            matches = [r for r in pending if r.id == int(wanted)]
        else:
            matches = [r for r in pending if wanted in r.text.lower()]
        if not matches:
            return ToolResult(False, f"I can't find a reminder matching \"{one_line(which)}\". "
                                     "Say \"what are my reminders\" to see them.")
        if len(matches) > 1:
            listed = "; ".join(f"{r.id}. {r.due:%H:%M} {r.text}" for r in matches[:5])
            return ToolResult(False, f"Several reminders match: {listed}. Which number?")
        reminder = self.scheduler.cancel(matches[0].id)
        return ToolResult(True, f"Cancelled the reminder for {reminder.due:%H:%M}: {reminder.text}", reminder)
