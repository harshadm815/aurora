"""
The Gmail Assistant's answers: an overview of the inbox, today's important mail, unanswered threads,
searches and summaries. Read-only. Everything taken from emails is cleaned (tools/untrusted.py) and
shown as data; nothing in an email can make AURORA do anything.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

from aurora.email import classify, extract
from aurora.email.gmail import Email, GmailClient
from aurora.tools.untrusted import one_line

FILLER = re.compile(r"\b(aurora|please|can you|could you|find|search( for)?|look for|show( me)?|open|read|the|an?|"
                    r"e-?mails?|mails?|message|messages|in gmail|my|about|regarding|for|from my inbox|"
                    r"conversation|thread)\b", re.I)


@dataclass
class Item:
    email: Email
    sorted: classify.Sorted

    @property
    def category(self) -> str:
        return self.sorted.category


class Inbox:
    def __init__(self, client: GmailClient, work_domains: tuple[str, ...] = (), today=date.today):
        self.client = client
        self.work_domains = tuple(d.lower() for d in work_domains)
        self.today = today
        self._me = ""

    def me(self) -> str:
        if not self._me:
            self._me = self.client.address().lower()
        return self._me

    def _items(self, query: str, limit: int) -> list[Item]:
        items = []
        for message_id in self.client.search(query, limit):
            email = self.client.message(message_id)
            items.append(Item(email, classify.sort(email, self.me(), self.work_domains)))
        return items

    def recent(self, days: int = 1, limit: int = 30) -> list[Item]:
        return self._items(f"in:inbox newer_than:{max(1, days)}d", limit)

    def important(self, days: int = 1) -> list[Item]:
        wanted = (classify.IMPORTANT, classify.WORK, classify.MEETINGS, classify.PERSONAL)
        return [i for i in self.recent(days) if i.category in wanted and not i.sorted.suspicious]

    def unanswered(self, days: int = 7, limit: int = 12) -> list[Item]:
        """Conversations where someone wrote to you last and you haven't replied (people, not newsletters)."""
        me = self.me()
        seen, waiting = set(), []
        for item in self._items(f"in:inbox -from:me newer_than:{days}d -category:promotions -category:social", 40):
            if item.email.thread_id in seen or item.category in (classify.PROMOTIONS, classify.NEWSLETTERS,
                                                                 classify.NOTIFICATIONS, classify.SUSPICIOUS):
                continue
            seen.add(item.email.thread_id)
            thread = self.client.thread(item.email.thread_id)
            if thread and thread[-1].sender != me and me in thread[-1].to + thread[-1].cc:
                waiting.append(item)
            if len(waiting) >= limit:
                break
        return waiting

    def find(self, request: str, limit: int = 8) -> list[Item]:
        """Emails matching what you said ("the email about tomorrow's meeting")."""
        words = [w for w in re.findall(r"[\w@.'-]+", FILLER.sub(" ", request)) if len(w) > 1]
        query = " ".join(words[:8]) or "in:inbox"
        return self._items(query, limit)

    def overview(self, items: list[Item]) -> dict:
        counts = {c: 0 for c in classify.CATEGORIES}
        for item in items:
            counts[item.category] += 1
        return {"total": len(items), "unread": sum(1 for i in items if i.email.unread), "counts": counts,
                "suspicious": [i for i in items if i.category == classify.SUSPICIOUS]}

    def digest(self, email: Email) -> dict:
        """What an email (opened in full) says, asks and when: all found in its text, not guessed."""
        today = self.today()
        body = email.body or email.snippet
        return {"summary": [one_line(s) for s in extract.summary(body)],
                "requests": [one_line(s) for s in extract.requests(body)],
                "dates": extract.dates(extract.own_text(body), today),
                "deadlines": extract.deadlines(body, today)}


def line(item: Item, number: int | None = None) -> str:
    """One email as one line: "2. Alex — Tomorrow's meeting (10:15, unread)"."""
    email = item.email
    when = email.date.astimezone().strftime("%H:%M" if email.date and email.date.date() == datetime.now().date()
                                            else "%d %b") if email.date else ""
    flags = ", ".join(f for f in (when, "unread" if email.unread else "", "📎" if email.attachments else "") if f)
    prefix = f"{number}. " if number is not None else ""
    return f"{prefix}{one_line(email.who)[:40]} — {one_line(email.subject)[:90]}" + (f" ({flags})" if flags else "")
