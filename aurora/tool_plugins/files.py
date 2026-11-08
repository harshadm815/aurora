"""
Files tool: "organise my documents by type", "tidy my documents by month",
"undo my last tidy-up", "which folders can you tidy?".

Every tidy-up is shown first and needs your yes; see aurora/automation/files.py
for the rules (allowed folders only, nothing deleted or overwritten, undo).
"""

from aurora.automation.files import FileError, Organizer, allowed_folders, find_folder, make_plan
from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

PREVIEW_LINES = 8


class FilesTool(BaseTool):

    required_permissions = (Permission.FILESYSTEM_READ, Permission.FILESYSTEM_WRITE)

    def __init__(self, organizer: Organizer | None = None):
        super().__init__("Files")
        self.organizer = organizer or Organizer()

    def execute(self, action: str = "organize", folder: str = "", by: str = "type", **_) -> ToolResult:
        try:
            if action == "folders":
                names = ", ".join(str(f) for f in allowed_folders()) or "none"
                return ToolResult(True, f"I'm allowed to tidy: {names}.")
            if action == "undo":
                return self.undo_last()
            return self.organize(folder, by)
        except FileError as error:
            return ToolResult(False, str(error))

    def organize(self, folder: str, by: str) -> ToolResult:
        plan = make_plan(find_folder(folder), "month" if by == "month" else "type")
        note = plan.skipped_note()
        if not plan.moves:
            return ToolResult(True, f"Nothing to tidy in {plan.folder.name}." + (f" {note}" if note else ""), plan)

        examples = [f"  {m.source.name} → {m.destination.parent.name}/{m.destination.name}"
                    for m in plan.moves[:PREVIEW_LINES]]
        if len(plan.moves) > PREVIEW_LINES:
            examples.append(f"  … and {len(plan.moves) - PREVIEW_LINES} more")
        lines = [f"I'd move {plan.summary()}.", *examples]
        if note:
            lines.append(note)
        lines.append("Nothing is deleted or overwritten, and you can undo it. Shall I? (yes / no)")

        def carry_out() -> ToolResult:
            return self._report(plan, self.organizer.execute(plan))

        return ToolResult(True, "\n".join(lines), plan, confirm=carry_out)

    def _report(self, plan, outcome) -> ToolResult:
        undo_hint = ' Say "undo that" to put them back.'
        if outcome.error or outcome.cancelled:
            why = f"then stopped: {outcome.error}" if outcome.error else "then stopped because you asked"
            message = (f"Moved {outcome.moved} of {outcome.planned} files in {plan.folder.name}, {why}. "
                       "Nothing was lost." + (undo_hint if outcome.moved else ""))
            result = ToolResult(outcome.moved > 0 and not outcome.error, message, outcome)
        else:
            groups = len(plan.groups())
            message = (f"Moved {outcome.moved} files in {plan.folder.name} into {groups} "
                       f"folder{'s' if groups != 1 else ''}." + undo_hint)
            result = ToolResult(True, message, outcome)
        if outcome.moved:
            result.undo = lambda: self._undo_batch(outcome.batch, plan.folder.name)
            result.undo_label = f"move the {outcome.moved} files back in {plan.folder.name}"
        return result

    def _undo_batch(self, batch: str, folder: str) -> ToolResult:
        back, problems = self.organizer.undo(batch)
        message = f"Moved {back} file{'s' if back != 1 else ''} back in {folder}."
        if problems:
            message += " Not moved back: " + "; ".join(problems[:5]) + ("…" if len(problems) > 5 else ".")
        return ToolResult(not problems, message, back)

    def undo_last(self) -> ToolResult:
        last = self.organizer.last_batch()
        if last is None:
            return ToolResult(False, "There's no tidy-up to undo.")
        batch, count, folder = last
        return ToolResult(True, f"Shall I move the {count} files from the last tidy-up of {folder} back? (yes / no)",
                          last, confirm=lambda: self._undo_batch(batch, folder))
