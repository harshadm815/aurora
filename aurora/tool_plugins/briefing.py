"""
Briefing: what's coming up today, from AURORA's own records.

    "give me my briefing" · "brief me" · "what's on today" · "what do I have today"

Built only from real data, read on this PC: pending reminders (today's,
and any that are overdue), scheduled tasks that will run today, and the
latest research session if it's from the last two days. Nothing is
invented or looked up online, so it's safe to speak aloud.

``summary_line()`` is the one-sentence version the Wake Engine's greeting
uses to offer a briefing ("You have two reminders today. Would you like a
briefing?").
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from aurora.kernel.permissions import Permission
from aurora.tools.tool import BaseTool, ToolResult

RESEARCH_DAYS = 2
WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def count(n: int, thing: str) -> str:
    """'two reminders', 'one scheduled task' (words up to ten: these are spoken)."""
    number = WORDS[n] if 0 <= n < len(WORDS) else str(n)
    return f"{number} {thing}{'' if n == 1 else 's'}"


def join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


@dataclass
class Briefing:
    now: datetime
    overdue: list = field(default_factory=list)          # reminders past due, still pending
    today: list = field(default_factory=list)            # reminders due later today
    tasks: list = field(default_factory=list)            # (when, name) scheduled tasks running today
    research: tuple | None = None                        # (project, session) from the last two days

    @property
    def empty(self) -> bool:
        return not (self.overdue or self.today or self.tasks or self.research)


def gather(reminders, tasks, projects, now: datetime | None = None) -> Briefing:
    now = now or datetime.now()
    end_of_day = datetime.combine(now.date(), datetime.max.time())
    brief = Briefing(now)
    for reminder in sorted(reminders, key=lambda r: r.due):
        if reminder.due < now:
            brief.overdue.append(reminder)
        elif reminder.due <= end_of_day:
            brief.today.append(reminder)
    for task in tasks:
        if not getattr(task, "active", True):
            continue
        when = task.next_run(now)
        if when is not None and when <= end_of_day:
            brief.tasks.append((when, task.name))
    brief.tasks.sort(key=lambda item: item[0])
    latest = max(((s.time, p, s) for p in projects for s in p.sessions), default=None, key=lambda x: x[0])
    if latest is not None and now - latest[0] <= timedelta(days=RESEARCH_DAYS):
        brief.research = (latest[1], latest[2])
    return brief


def summary_line(brief: Briefing) -> str:
    """One sentence for the greeting, or "" if there's nothing to report."""
    parts = []
    reminders = len(brief.today) + len(brief.overdue)
    if reminders:
        parts.append(count(reminders, "reminder"))
    if brief.tasks:
        parts.append(count(len(brief.tasks), "scheduled task"))
    if brief.research:
        parts.append("a research update")
    if not parts:
        return ""
    return f"You have {join(parts)} today."


def text(brief: Briefing) -> str:
    now = brief.now
    lines = [f"Here's your briefing for {now:%A %d %B}."]
    if brief.overdue:
        items = "; ".join(f"{r.text} (was due {r.due:%H:%M}" + ("" if r.due.date() == now.date()
                                                               else f" on {r.due:%a %d %b}") + ")"
                          for r in brief.overdue)
        lines.append(f"Overdue: {items}.")
    if brief.today:
        items = "; ".join(f"at {r.due:%H:%M}, {r.text}" for r in brief.today)
        lines.append(f"Reminders today: {items}.")
    if brief.tasks:
        items = "; ".join(f"at {when:%H:%M}, {name}" for when, name in brief.tasks)
        lines.append(f"Scheduled today: {items}.")
    if brief.research:
        project, session = brief.research
        when = "today" if session.time.date() == now.date() else f"on {session.time:%A}"
        summary = session.summary.strip().rstrip(".")
        lines.append(f"Research: your latest session on {project.name} was {when}: {summary}.")
    if brief.empty:
        lines.append("Nothing is scheduled for the rest of today, and there are no reminders or recent research.")
    return "\n".join(lines)


class BriefingTool(BaseTool):
    """Reads the Reminders, Schedule and Research tools' records (attached by the engine)."""

    required_permissions = (Permission.MEMORY_READ,)

    def __init__(self):
        super().__init__("Briefing")
        self.tools = None

    def attach_tools(self, tools) -> None:
        self.tools = tools

    def gather(self, now: datetime | None = None) -> Briefing:
        get = (lambda name: self.tools.get(name)) if self.tools is not None else (lambda name: None)
        reminders, schedule, research = get("Reminders"), get("Schedule"), get("Research")
        pending = reminders.scheduler.pending() if reminders is not None else []
        tasks = schedule.tasks.all() if schedule is not None else []
        projects = research.log.projects() if research is not None else []
        return gather(pending, tasks, projects, now)

    def execute(self, **_) -> ToolResult:
        brief = self.gather()
        return ToolResult(True, text(brief), brief)
