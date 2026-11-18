"""
The Gmail Assistant in Chat (read-only): the Email intent (routing/email_rules.py) comes here.

    check my Gmail                       overview: new mail by category, unread, possibly suspicious
    show me today's important emails     people, school/work and meetings, newest first
    do I have any unanswered emails?     conversations where someone wrote to you last
    find the email about tomorrow's meeting
    summarise email 2 / this conversation    what it says, asks, and dates found in it
    disconnect Gmail                     revokes AURORA's access (asks first)

Connecting is done in Settings → Gmail (it opens your browser for Google's sign-in). Needs the
"network" and "email.read" permissions; uses the internet gate as component "Gmail".
"""

import re

from aurora.config import settings
from aurora.email import classify
from aurora.email.gmail import GmailClient, GmailError
from aurora.email.inbox import Inbox, line
from aurora.email.oauth import AuthError, GoogleAuth, load_client
from aurora.email.secrets import SecretFile
from aurora.kernel.permissions import Permission
from aurora.network import gate
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool
from aurora.tools.untrusted import one_line

COMPONENT = "Gmail"
_UNANSWERED = re.compile(r"\b(unanswered|not (yet )?(answered|replied)|haven'?t (answered|replied)|need(s)? (a )?"
                         r"(reply|response)|waiting for (my )?(reply|answer)|awaiting)\b", re.I)
_IMPORTANT = re.compile(r"\bimportant\b", re.I)
_FIND = re.compile(r"\b(find|search|look for|where is|which email|the e-?mail (about|from|regarding))\b", re.I)
_SUMMARY = re.compile(r"\b(summari[sz]e|summary of|explain|what does (it|this|the email) say|read)\b", re.I)
_NUMBER = re.compile(r"\b(?:email|mail|message|number|no\.?|#)\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)? (?:e-?mail|one)\b",
                     re.I)
_REPORT = re.compile(r"\b(e-?mail|mail|inbox|gmail) (report|digest|briefing)\b|\bend[- ]of[- ](the[- ])?day\b",
                     re.I)
_DISCONNECT = re.compile(r"\b(disconnect|sign out of|log out of|revoke|unlink)\b.*\b(g-?mail|e-?mail|google)\b", re.I)


def service():
    """(auth, client) for the configured account. The http client goes through the internet gate."""
    http = gate.client(COMPONENT, timeout=30)
    auth = GoogleAuth(load_client(settings.GMAIL_CLIENT_FILE), SecretFile(settings.GMAIL_TOKEN_FILE), http)
    return auth, GmailClient(auth, http)


class GmailTool(BaseTool):
    required_permissions = (Permission.NETWORK, Permission.EMAIL_READ)

    def __init__(self, inbox_factory=None):
        super().__init__(COMPONENT)
        self._factory = inbox_factory
        self._inbox: Inbox | None = None
        self._auth = None
        self.shown: list = []                     # the last list, so "email 2" means something

    def inbox(self) -> Inbox:
        if self._inbox is None:
            if self._factory is not None:
                self._inbox = self._factory()
            else:
                self._auth, client = service()
                self._inbox = Inbox(client, tuple(settings.GMAIL_WORK_DOMAINS))
        return self._inbox

    def auth(self):
        """The Google sign-in in use (None when an inbox was given directly, as in tests)."""
        self.inbox()
        return self._auth

    def execute(self, request: str = "", **arguments) -> ToolResult:
        text = (request or "").strip()
        try:
            if _DISCONNECT.search(text):
                return self._disconnect()
            if _REPORT.search(text):
                return self._report()
            if _UNANSWERED.search(text):
                return self._list(self.inbox().unanswered(), "Conversations waiting for your reply (last 7 days)",
                                  "Nothing is waiting for your reply from the last 7 days.")
            if _SUMMARY.search(text) and (self.shown or _NUMBER.search(text)):
                return self._summary(text)
            if _FIND.search(text):
                return self._list(self.inbox().find(text), "Emails that match",
                                  "No emails match that. Try other words, a sender's name or a date.")
            if _IMPORTANT.search(text):
                days = 7 if re.search(r"\b(this )?week\b", text, re.I) else 1
                return self._list(self.inbox().important(days),
                                  "Important emails " + ("this week" if days == 7 else "today"),
                                  "No important emails " + ("this week." if days == 7 else "today."))
            return self._overview()
        except (GmailError, AuthError) as error:
            return ToolResult(False, str(error), retryable=getattr(error, "retryable", False))

    # ------------------------------------------------------------------ answers

    def _overview(self) -> ToolResult:
        inbox = self.inbox()
        items = inbox.recent(days=1)
        o = inbox.overview(items)
        if not items:
            self.shown = []
            return ToolResult(True, "No new email in your inbox today.")
        busy = [f"{name}: {n}" for name, n in o["counts"].items() if n]
        lines = [f"Gmail, today: {o['total']} new ({o['unread']} unread).", "  " + " · ".join(busy)]
        worth = [i for i in items if i.category in (classify.IMPORTANT, classify.WORK, classify.MEETINGS,
                                                     classify.PERSONAL)][:6]
        self.shown = worth
        if worth:
            lines += ["", "Worth a look:"] + ["  " + line(item, n) for n, item in enumerate(worth, 1)]
        for item in o["suspicious"][:3]:
            lines += ["", f"⚠ Possibly suspicious: {line(item)}"] + [f"   – {s}" for s in item.sorted.suspicious[:3]]
        lines += ["", "Say \"summarise email 1\", \"unanswered emails\" or \"find the email about …\"."]
        return ToolResult(True, "\n".join(lines), data={"overview": {k: v for k, v in o.items() if k != "suspicious"}})

    def _list(self, items: list, title: str, empty: str) -> ToolResult:
        self.shown = items
        if not items:
            return ToolResult(True, empty)
        lines = [f"{title}:"] + ["  " + line(item, n) for n, item in enumerate(items, 1)]
        lines += ["", "Say \"summarise email 1\" to hear what one says."]
        return ToolResult(True, "\n".join(lines), data={"count": len(items)})

    def _summary(self, text: str) -> ToolResult:
        match = _NUMBER.search(text)
        number = int(next(g for g in match.groups() if g)) if match else 1
        if not self.shown:
            return ToolResult(False, "Which email? Ask \"check my Gmail\" or \"find the email about …\" first.")
        if not 1 <= number <= len(self.shown):
            return ToolResult(False, f"There's no email {number} in the last list (it has {len(self.shown)}).")
        inbox = self.inbox()
        chosen = self.shown[number - 1].email
        thread = inbox.client.thread(chosen.thread_id) or [inbox.client.message(chosen.id, full=True)]
        me = inbox.me()
        theirs = [m for m in thread if m.sender != me]
        latest = theirs[-1] if theirs else thread[-1]          # what they said, not your own reply
        found = inbox.digest(latest)
        people = sorted({one_line(m.who) for m in thread})
        lines = [f"“{one_line(latest.subject)}” — {len(thread)} message{'s' if len(thread) != 1 else ''} with "
                 f"{', '.join(people[:4])}{'…' if len(people) > 4 else ''}.", "",
                 f"In {one_line(latest.who)}'s latest message (its own words, shortened):"]
        lines += [f"  · {s}" for s in found["summary"]] or ["  (no text)"]
        if found["requests"]:
            lines += ["", "It asks:"] + [f"  · {s}" for s in found["requests"]]
        if found["deadlines"]:
            lines += ["", "Deadlines mentioned:"] + [f"  · {d.when:%a %d %b}{' ' + d.time if d.time else ''} "
                                                     f"(\"{one_line(d.text)}\")" for d in found["deadlines"]]
        elif found["dates"]:
            lines += ["", "Dates mentioned:"] + [f"  · {d.when:%a %d %b}{' ' + d.time if d.time else ''} "
                                                 f"(\"{one_line(d.text)}\")" for d in found["dates"][:4]]
        if latest.attachments:
            lines += ["", "Attachments: " + ", ".join(one_line(a) for a in latest.attachments[:5])]
        signs = classify.suspicious_signs(latest)
        if signs:
            lines += ["", "⚠ Possibly suspicious:"] + [f"  – {s}" for s in signs]
        lines += ["", "Found in the email's text, not checked: dates and requests may be read wrongly."]
        return ToolResult(True, "\n".join(lines))

    def _report(self) -> ToolResult:
        """Today's end-of-day report: the one already made, or made now."""
        from datetime import date as today_
        from aurora.email.daily import EmailReporter
        reporter = EmailReporter(inbox_factory=self.inbox)
        report = reporter.latest()
        if report is None or report.day != today_.today().isoformat():
            report = reporter.make(scheduled=False)
        return ToolResult(True, report.text(), data={"headline": report.headline()})

    def _disconnect(self) -> ToolResult:
        def go():
            auth = self._auth or service()[0]
            self._inbox = None
            return ToolResult(True, auth.disconnect())
        return ToolResult(True, "Disconnect AURORA from Gmail? Google will revoke its access and the sign-in is "
                                "deleted from this PC. (yes / no)", confirm=go)
