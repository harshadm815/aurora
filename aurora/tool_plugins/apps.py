"""
Apps tool: "open notepad", "launch the calculator", "close notepad",
"which programs can you open?". See aurora/automation/apps.py for the rules.
"""

from aurora.automation.apps import AppError, Launcher
from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool


class AppsTool(BaseTool):

    required_permissions = (Permission.APPS_LAUNCH,)

    def __init__(self, launcher: Launcher | None = None):
        super().__init__("Apps")
        self.launcher = launcher or Launcher()

    def execute(self, action: str = "open", app: str = "", **_) -> ToolResult:
        try:
            if action == "list":
                return self.list()
            target = self.launcher.find(app)
            if action == "close":
                return self.close(target)
            self.launcher.command(target)            # check it exists before asking or opening
        except AppError as error:
            return ToolResult(False, str(error))

        def carry_out() -> ToolResult:
            try:
                closable = self.launcher.open(target)
            except (AppError, OSError) as error:
                reason = error if isinstance(error, AppError) else (error.strerror or type(error).__name__)
                return ToolResult(False, f"{target.name} didn't open ({reason}).")
            if not closable:
                return ToolResult(True, f"Opened {target.name}. (Windows runs it separately, so close it "
                                        "yourself when you're done.)", target.name)
            result = ToolResult(True, f"Opened {target.name}.", target.name)
            result.undo = lambda: self._close_now(target)
            result.undo_label = f"close {target.name} (unsaved work in it would be lost)"
            return result

        if target.confirm:
            return ToolResult(True, f"Shall I open {target.name}? (yes / no)", target.name, confirm=carry_out)
        return carry_out()

    def _close_now(self, target) -> ToolResult:
        closed = self.launcher.close(target)
        if not closed:
            return ToolResult(False, f"{target.name} is already closed.")
        return ToolResult(True, f"Closed {target.name}.", closed)

    def close(self, target) -> ToolResult:
        if not self.launcher.running(target):
            return ToolResult(False, f"I only close programs I opened in this session, and {target.name} isn't "
                                     "one of them (or it's already closed).")
        return ToolResult(True, f"Shall I close {target.name}? Unsaved work in it will be lost. (yes / no)",
                          target.name, confirm=lambda: self._close_now(target))

    def list(self) -> ToolResult:
        lines = []
        for app in self.launcher.apps:
            try:
                self.launcher.command(app)
                state = " (open, started by me)" if self.launcher.running(app) else ""
            except AppError:
                state = " (not found on this PC)"
            lines.append(f"  - {app.name}{state}")
        return ToolResult(True, "Programs I can open:\n" + "\n".join(lines), [a.name for a in self.launcher.apps])
