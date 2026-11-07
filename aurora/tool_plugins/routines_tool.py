"""
Routines tool: teach, list and delete routines (procedural memory).

The engine checks a routine's steps before it is saved
(engine.check_routine) and runs routines when their name is said.
"""

from aurora.kernel.permissions import Permission
from aurora.memory.routines import normalize_name
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool


class RoutinesTool(BaseTool):

    required_permissions = (Permission.MEMORY_READ, Permission.MEMORY_WRITE)

    def __init__(self):
        super().__init__("Routines")
        self.routines = None
        self.check = None          # engine.check_routine(name, steps) -> problem or None

    def attach_memory(self, engine) -> None:
        self.routines = engine.routines
        self.check = engine.check_routine

    def execute(self, action: str = "", name: str = "", steps: str = "", **_) -> ToolResult:
        if self.routines is None:
            return ToolResult(False, "Routines aren't available.")

        if action == "list":
            routines = self.routines.all()
            if not routines:
                return ToolResult(True, "You haven't taught me any routines yet. For example: "
                                        "\"when I say morning briefing, check the weather in Pune and list my reminders\".")
            lines = "\n".join(f"  - “{r.name}”: {r.steps}" for r in routines)
            return ToolResult(True, f"Routines you've taught me:\n{lines}", routines)

        if action == "create":
            problem = self.check(name, steps) if self.check else None
            if problem:
                return ToolResult(False, problem)
            try:
                routine, replaced = self.routines.save(name, steps)
            except ValueError as error:
                return ToolResult(False, str(error))
            verb = "Updated" if replaced else "Okay"
            return ToolResult(True, f"{verb}. When you say “{routine.name}”, I'll: {routine.steps}", routine)

        if action == "delete":
            routine = self.routines.get(name)
            if routine is None:
                return ToolResult(False, f"I don't have a routine called “{normalize_name(name)}”.")

            def delete() -> ToolResult:
                self.routines.delete(routine.name)
                return ToolResult(True, f"Done. I deleted the routine “{routine.name}”.")

            return ToolResult(True, f"I'll delete the routine “{routine.name}” ({routine.steps}). "
                                    "Should I go ahead? (yes / no)", routine, confirm=delete)

        return ToolResult(False, "I'm not sure what you'd like to do with routines.")
