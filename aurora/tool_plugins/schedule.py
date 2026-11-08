"""
Schedule tool: add, list, pause, resume and delete scheduled tasks.

    every weekday at 18:30 turn on the desk light
    every day at 7 run my morning briefing
    every evening at 21:00 remind me to take my medicine
    every morning at 8 check my sensors
    what tasks are scheduled? · pause task 2 · resume task 2 · delete task 2

Adding a task always asks first, spelling out exactly what will run and
when. See aurora/automation/schedule.py for the rules.
"""

from aurora.automation.schedule import TaskStore
from aurora.config import settings
from aurora.devices.device import DeviceError
from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.tools.untrusted import one_line

WHAT_I_CAN = ("I can schedule reminders (\"every day at 9 pm remind me to …\"), routines you taught "
              "(\"every day at 7 run my morning briefing\"), sensor reports (\"every morning at 8 check my "
              "sensors\") and devices you marked as schedulable (\"every weekday at 18:30 turn on the desk light\").")


class ScheduleTool(BaseTool):

    required_permissions = (Permission.AUTOMATION,)

    def __init__(self, tasks: TaskStore | None = None):
        super().__init__("Schedule")
        self.tasks = tasks or TaskStore()
        self.engine = None

    def attach_engine(self, engine) -> None:
        self.engine = engine

    def execute(self, action: str = "list", kind: str = "", days: str = "", at: str = "", number: int | None = None,
                **details) -> ToolResult:
        if action == "list":
            return self.list()
        if action in ("pause", "resume", "delete"):
            return self.change(action, number)
        if action == "delete_all":
            return self.delete_all()
        return self.add(kind, days, at, details)

    # --- Adding ------------------------------------------------------------------------------

    def _describe(self, kind: str, details: dict) -> tuple[str, dict] | ToolResult:
        """(name, target) for a valid task, or a ToolResult explaining why not."""
        if kind == "reminder":
            text = one_line(details.get("text", ""))
            if not text:
                return ToolResult(False, "What should the reminder say?")
            return f"remind you: {text}", {"text": text}

        if kind == "sensors":
            quantity = details.get("quantity", "all")
            what = {"all": "report your room sensors", "air": "report the air in your room",
                    "comfort": "report how comfortable your room is"}.get(quantity, f"report the {quantity}")
            return what, {"quantity": quantity}

        if kind == "routine":
            name = (details.get("routine") or "").strip()
            routine = self.engine.routines.get(name) if self.engine is not None else None
            if routine is None:
                return ToolResult(False, f"I don't know a routine called “{one_line(name)}”. "
                                         "Teach it first (\"when I say …, …\").")
            return f"run your routine “{routine.name}”", {"routine": routine.name}

        if kind == "device":
            devices = self.engine.tool_manager.get("Devices") if self.engine is not None else None
            if devices is None:
                return ToolResult(False, "Device control isn't available.")
            try:
                target = devices.controller.find(details.get("device", ""))
                command = devices.controller.validate(target, details.get("device_action", ""),
                                                      details.get("value"))
            except DeviceError as error:
                return ToolResult(False, str(error))
            if not target.schedulable:
                return ToolResult(False, f"The {target.name} isn't marked as schedulable, so I won't switch it "
                                         "on a timetable. If you want that, add \"schedulable\": True to it in "
                                         "DEVICES in settings.")
            simulated = devices.controller.driver(target).simulated
            name = command.describe(target) + (" (simulated device)" if simulated else "")
            return name, {"device": target.name, "device_action": command.action, "value": command.value}

        return ToolResult(False, WHAT_I_CAN)

    def add(self, kind: str, days: str, at: str, details: dict) -> ToolResult:
        if not at:
            return ToolResult(False, "At what time? For example: \"every day at 7:30 …\".")
        described = self._describe(kind, details)
        if isinstance(described, ToolResult):
            return described
        name, target = described
        if len(self.tasks.all()) >= settings.SCHEDULE_MAX_TASKS:
            return ToolResult(False, f"You already have {settings.SCHEDULE_MAX_TASKS} scheduled tasks; "
                                     "delete one first.")
        from aurora.automation.schedule import describe_days

        when = f"{describe_days(days)} at {at}"

        def create() -> ToolResult:
            task = self.tasks.add(name, kind, target, days, at)
            message = f"Scheduled task {task.id} added: {task.when()}, {task.name}."
            result = ToolResult(True, message, task)
            result.undo = lambda: self._delete(task.id)
            result.undo_label = f"delete scheduled task {task.id}"
            return result

        return ToolResult(True, f"Shall I add a scheduled task: {when}, {name}? It will run at that time "
                                "without asking each time; you can pause or delete it whenever you like. (yes / no)",
                          target, confirm=create)

    # --- Managing -----------------------------------------------------------------------------

    def list(self) -> ToolResult:
        tasks = self.tasks.all()
        if not tasks:
            return ToolResult(True, "You have no scheduled tasks. " + WHAT_I_CAN, [])
        return ToolResult(True, "Scheduled tasks:\n" + "\n".join(f"  {task.line()}" for task in tasks), tasks)

    def _delete(self, task_id: int) -> ToolResult:
        if not self.tasks.delete(task_id):
            return ToolResult(False, f"There's no scheduled task {task_id}.")
        return ToolResult(True, f"Deleted scheduled task {task_id}.")

    def change(self, action: str, number: int | None) -> ToolResult:
        task = self.tasks.get(number) if number is not None else None
        if task is None:
            which = f"There's no scheduled task {number}. " if number is not None else "Which task? "
            return ToolResult(False, which + "Say \"what tasks are scheduled\" to see their numbers.")
        if action == "delete":
            return self._delete(task.id)
        self.tasks.set_active(task.id, action == "resume")
        verb = "Resumed" if action == "resume" else "Paused"
        return ToolResult(True, f"{verb} scheduled task {task.id} ({task.when()}, {task.name}).", task)

    def delete_all(self) -> ToolResult:
        tasks = self.tasks.all()
        if not tasks:
            return ToolResult(True, "You have no scheduled tasks.")

        def delete() -> ToolResult:
            for task in tasks:
                self.tasks.delete(task.id)
            return ToolResult(True, f"Deleted {len(tasks)} scheduled task{'s' if len(tasks) != 1 else ''}.")

        return ToolResult(True, f"Shall I delete all {len(tasks)} scheduled tasks? (yes / no)", tasks, confirm=delete)
