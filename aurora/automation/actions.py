"""
Every action AURORA can take on your behalf, with its rules.

For each action: the permission it needs, whether AURORA asks you first,
how it can be reversed, and whether a scheduled task may run it. This
table is the contract: the tools follow it, tests check them against it,
and the Command Centre shows it (Automation page).

Read-only requests (status, lists, readings) aren't actions and aren't
listed. Anything not in this table can't be done by AURORA at all.
"""

from dataclasses import dataclass

ALWAYS, CONFIGURABLE, NEVER = "always", "unless you turned it off for that item", "no"


@dataclass(frozen=True)
class ActionSpec:
    name: str                 # what the audit trail calls it
    tool: str                 # the tool that does it (and whose permission is checked)
    does: str
    permissions: tuple[str, ...]
    asks_first: str           # ALWAYS / CONFIGURABLE / NEVER
    undo: str                 # how to reverse it, or why it can't be
    schedulable: bool = False


ACTIONS: tuple[ActionSpec, ...] = (
    ActionSpec("devices.change", "Devices", "Switch or set a configured device (on, off, toggle, level)",
               ("hardware_control",), CONFIGURABLE,
               "\"undo that\" puts the device back as it was (asks first)",
               schedulable=True),
    ActionSpec("apps.open", "Apps", "Open a program from the APPS list (no arguments from what you say)",
               ("apps.launch",), CONFIGURABLE, "\"close <program>\" closes it if AURORA started it (asks first)"),
    ActionSpec("apps.close", "Apps", "Close a program AURORA opened this session",
               ("apps.launch",), ALWAYS, "Can't be undone: unsaved work in that program is lost"),
    ActionSpec("files.organize", "Files", "Move files in an allowed folder into subfolders (by type or month)",
               ("filesystem.read", "filesystem.write"), ALWAYS,
               "\"undo that\" moves every file back (asks first); nothing is ever deleted or overwritten"),
    ActionSpec("files.undo", "Files", "Put the files from the last tidy-up back",
               ("filesystem.read", "filesystem.write"), ALWAYS, "Tidy the folder again"),
    ActionSpec("reminders.add", "Reminders", "Set a reminder or timer (shown when due, kept if AURORA closes)",
               (), NEVER, "\"cancel reminder 2\" or \"cancel my reminders\"", schedulable=True),
    ActionSpec("reminders.cancel", "Reminders", "Cancel one or all reminders",
               (), NEVER, "Set the reminder again"),
    ActionSpec("schedule.add", "Schedule", "Add a task that runs at set times without asking each time",
               ("automation",), ALWAYS, "\"delete task 2\" or \"pause task 2\""),
    ActionSpec("schedule.change", "Schedule", "Pause, resume or delete a scheduled task",
               ("automation",), "only for \"delete all\"", "Resume or add it again"),
    ActionSpec("schedule.run", "Schedule", "Run a scheduled task at its time (a routine, reminder, sensor "
               "report or a device marked \"schedulable\")",
               ("automation", "plus the task's own"), NEVER, "Depends on the task (devices: \"undo that\")"),
    ActionSpec("memory.forget", "Forget", "Delete memories you name", ("memory.read", "memory.write"), ALWAYS,
               "Can't be undone: deleted memories are gone"),
    ActionSpec("routines.delete", "Routines", "Delete a routine you taught", ("memory.read", "memory.write"),
               ALWAYS, "Teach it again"),
)

BY_NAME = {spec.name: spec for spec in ACTIONS}
# Tools whose results are written to the audit trail.
AUDITED_TOOLS = {spec.tool for spec in ACTIONS}


def action_name(tool: str, arguments: dict) -> str:
    """The audit name of a tool call: "devices.change", "files.organize", ..."""
    action = str(arguments.get("action", ""))
    tool_key = tool.lower()
    if tool == "Devices":
        return "devices.change" if action in ("on", "off", "toggle", "set") else f"devices.{action or 'list'}"
    if tool == "Forget":
        return "memory.forget"
    if tool == "Schedule" and action in ("pause", "resume", "delete", "delete_all"):
        return "schedule.change"
    return f"{tool_key}.{action}" if action else tool_key
