from aurora.automation.reminders import ReminderScheduler, due_time, in_words
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.tools.untrusted import one_line

SESSION_NOTE = "Reminders only work while AURORA is running."


class RemindersTool(BaseTool):
    """Set, list and cancel reminders and timers (this session only)."""

    def __init__(self, scheduler: ReminderScheduler | None = None):
        super().__init__("Reminders")
        self.scheduler = scheduler or ReminderScheduler()

    def execute(self, action: str = "add", text: str = "", seconds: int | None = None,
                at: str | None = None, timer: bool = False, **_) -> ToolResult:
        if action == "list":
            pending = self.scheduler.pending()
            if not pending:
                return ToolResult(True, "You have no reminders set.", [])
            lines = [f"  - {r.due:%H:%M} {r.text}" for r in pending]
            return ToolResult(True, "Your reminders:\n" + "\n".join(lines), pending)

        if action == "cancel":
            count = self.scheduler.cancel_all()
            return ToolResult(True, f"Cancelled {count} reminder{'s' if count != 1 else ''}.", count)

        if seconds is None and at is None:
            return ToolResult(False, "When should I remind you? For example: \"in 10 minutes\" or \"at 5 pm\".")

        now = self.scheduler.clock()
        due = due_time(now, seconds, at)
        wait = in_words((due - now).total_seconds())
        text = one_line(text) or (f"Your timer for {wait} is up." if timer else "Time's up.")

        try:
            self.scheduler.add(text, due)
        except ValueError as error:
            return ToolResult(False, str(error))

        if timer:
            message = f"Timer set for {wait} (until {due:%H:%M})."
        else:
            ending = "" if text[-1:] in ".!?" else "."
            message = f"Okay, I'll remind you at {due:%H:%M} (in {wait}): {text}{ending}"
        return ToolResult(True, f"{message} {SESSION_NOTE}", due)
