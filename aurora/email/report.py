"""
The end-of-day email report (Docs/COMMUNICATIONS_PLAN.md, phase 4): what today's mail brought, what
needs you, and what to look at tomorrow. Built only from what Gmail says and what is found in the
emails' text; nothing is claimed that the data doesn't show (AURORA can't see whether anyone read your
replies). Kept on this PC (encrypted), shown in Chat, and sent to the paired phone if you allow it.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from aurora.email import classify, extract
from aurora.email.inbox import Inbox, Item
from aurora.tools.untrusted import one_line

PEOPLE = (classify.IMPORTANT, classify.WORK, classify.MEETINGS, classify.PERSONAL)
MAX_OPENED = 12                                   # emails read in full (for deadlines and requests)


@dataclass
class Report:
    day: str                                       # "2026-11-17"
    made: str                                      # when it was prepared, ISO
    total: int = 0
    unread: int = 0
    counts: dict = field(default_factory=dict)
    important: list = field(default_factory=list)  # {"from", "subject", "unread"}
    meetings: list = field(default_factory=list)
    requests: list = field(default_factory=list)   # {"from", "subject", "asks"}
    deadlines: list = field(default_factory=list)  # {"when", "time", "from", "subject", "words"}
    awaiting: list = field(default_factory=list)   # conversations waiting for your reply
    suspicious: list = field(default_factory=list)
    unaddressed: int = 0                           # important and still unread
    tomorrow: list = field(default_factory=list)   # one line each
    made_on_schedule: bool = False                 # made at your daily time (not just asked for in Chat)

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(data: dict) -> "Report":
        report = Report(day=data.get("day", ""), made=data.get("made", ""))
        for key, value in data.items():
            if hasattr(report, key):
                setattr(report, key, value)
        return report

    def headline(self) -> str:
        """One line for a notification."""
        parts = [f"{self.total} new", f"{len(self.important)} important"]
        if self.awaiting:
            parts.append(f"{len(self.awaiting)} waiting for your reply")
        if self.deadlines:
            parts.append(f"{len(self.deadlines)} deadline{'s' if len(self.deadlines) != 1 else ''}")
        return ", ".join(parts) + "."

    def text(self) -> str:
        """The whole report, as AURORA shows it in Chat (and the phone shows it)."""
        day = datetime.fromisoformat(self.day).strftime("%A %d %B") if self.day else "today"
        lines = [f"Email report — {day}", "",
                 "Inbox", f"  {self.total} received today, {self.unread} unread, {len(self.important)} important, "
                          f"{len(self.awaiting)} waiting for your reply."]
        busy = [f"{name}: {n}" for name, n in self.counts.items() if n]
        if busy:
            lines.append("  " + " · ".join(busy))
        if self.important:
            lines += ["", "Important"] + [f"  · {i['from']} — {i['subject']}" + (" (unread)" if i.get("unread") else "")
                                          for i in self.important[:8]]
        if self.meetings:
            lines += ["", "Meetings and invitations"] + [f"  · {m['from']} — {m['subject']}" for m in self.meetings[:6]]
        if self.requests:
            lines += ["", "Asked of you"] + [f"  · {r['from']}: {r['asks']}" for r in self.requests[:6]]
        if self.deadlines:
            lines += ["", "Deadlines mentioned"] + [
                f"  · {_day(d['when'])}{' ' + d['time'] if d.get('time') else ''} — {d['subject']} (\"{d['words']}\")"
                for d in self.deadlines[:8]]
        if self.awaiting:
            lines += ["", "Waiting for your reply"] + [f"  · {a['from']} — {a['subject']}" for a in self.awaiting[:8]]
        if self.suspicious:
            lines += ["", "⚠ Possibly suspicious (not opened, check before clicking anything)"] + [
                f"  · {s['from']} — {s['subject']}: {s['why']}" for s in self.suspicious[:4]]
        lines += ["", "Email activity",
                  f"  {self.unaddressed} important email{'s' if self.unaddressed != 1 else ''} still unread.",
                  "  Replies: AURORA can't send email yet (read-only), so none were sent or drafted."]
        lines += ["", "Tomorrow"] + ([f"  · {t}" for t in self.tomorrow] or ["  · Nothing found that's due."])
        lines += ["", "Dates and requests are found in the emails' text, not checked."]
        return "\n".join(lines)


def _day(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%a %d %b")
    except ValueError:
        return iso


def _who(item: Item) -> str:
    return one_line(item.email.who)[:40]


def _subject(item: Item) -> str:
    return one_line(item.email.subject)[:90]


def build(inbox: Inbox, now: datetime | None = None) -> Report:
    """Today's report from Gmail (reads at most MAX_OPENED emails in full)."""
    now = now or datetime.now()
    today = now.date()
    report = Report(day=today.isoformat(), made=now.isoformat(timespec="seconds"))
    items = inbox._items(f"in:inbox after:{today:%Y/%m/%d}", 60)
    overview = inbox.overview(items)
    report.total, report.unread = overview["total"], overview["unread"]
    report.counts = overview["counts"]
    people = [i for i in items if i.category in PEOPLE and not i.sorted.suspicious]
    report.important = [{"from": _who(i), "subject": _subject(i), "unread": i.email.unread} for i in people]
    report.meetings = [{"from": _who(i), "subject": _subject(i)} for i in items if i.category == classify.MEETINGS]
    report.suspicious = [{"from": _who(i), "subject": _subject(i), "why": (i.sorted.suspicious or ["Gmail: spam"])[0]}
                         for i in overview["suspicious"]]
    report.unaddressed = sum(1 for i in people if i.email.unread)
    for item in people[:MAX_OPENED]:                       # read in full: what's asked and by when
        email = inbox.client.message(item.email.id, full=True)
        body = email.body or email.snippet
        asks = extract.requests(body, limit=1)
        if asks:
            report.requests.append({"from": _who(item), "subject": _subject(item), "asks": one_line(asks[0])[:160]})
        for found in extract.deadlines(body, today):
            if found.when >= today:
                report.deadlines.append({"when": found.when.isoformat(), "time": found.time, "from": _who(item),
                                         "subject": _subject(item), "words": one_line(found.text)[:40]})
    report.deadlines.sort(key=lambda d: (d["when"], d["time"]))
    report.awaiting = [{"from": _who(i), "subject": _subject(i)} for i in inbox.unanswered()]
    tomorrow = (today + timedelta(days=1)).isoformat()
    report.tomorrow = ([f"Due tomorrow: {d['subject']} ({d['from']})" for d in report.deadlines if d["when"] == tomorrow]
                       + [f"Reply to {a['from']} — {a['subject']}" for a in report.awaiting[:3]]
                       + [f"Meeting: {m['subject']}" for m in report.meetings[:2]])
    return report
