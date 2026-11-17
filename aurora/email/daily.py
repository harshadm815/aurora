"""
Making the end-of-day email report at the time you chose (Settings → Gmail), and keeping the latest one.

The Command Centre checks once a minute while AURORA runs (also in the notification area). When the
time has passed and today's report isn't made yet (the PC may have been asleep at that time), it's
made in the background and announced where you chose: this PC, your phone, or both. Switching the
daily report on is your yes to AURORA contacting Gmail for it each day, also when internet access is
set to "ask". The latest report is kept encrypted (it lists senders and subjects).
"""

import logging
import re
from datetime import datetime

from aurora.config import settings
from aurora.email.report import Report, build
from aurora.email.secrets import SecretFile

logger = logging.getLogger(__name__)

PLACES = ("pc", "phone", "both")


def parse_time(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*([01]?\d|2[0-3]):([0-5]\d)\s*", value or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


class EmailReporter:
    def __init__(self, store: SecretFile | None = None, inbox_factory=None, approve=None):
        self.store = store or SecretFile(settings.EMAIL_REPORT_FILE)
        self.inbox_factory = inbox_factory
        self.approve = approve
        self.busy = False

    def latest(self) -> Report | None:
        data = self.store.load()
        return Report.from_dict(data) if data else None

    def due(self, preferences: dict, now: datetime | None = None) -> bool:
        """True when the daily report is on, its time has passed today, and today's isn't made yet."""
        if not preferences.get("email_report") or self.busy:
            return False
        at = parse_time(preferences.get("email_report_time", ""))
        if at is None:
            return False
        now = now or datetime.now()
        if (now.hour, now.minute) < at:
            return False
        latest = self.latest()
        return latest is None or latest.day != now.date().isoformat() or not latest.made_on_schedule

    def make(self, now: datetime | None = None, scheduled: bool = True) -> Report:
        """Builds today's report (blocking: run it in the background) and keeps it."""
        self.busy = True
        try:
            if self.approve is not None:
                self.approve()                         # the daily report is the user's yes to contacting Gmail
            inbox = self.inbox_factory() if self.inbox_factory else _inbox()
            report = build(inbox, now)
            data = report.to_dict()
            data["made_on_schedule"] = scheduled
            self.store.save(data)
            logger.info("Email report made (%s)", "scheduled" if scheduled else "asked for")
            return Report.from_dict(data)
        finally:
            self.busy = False


def _inbox():
    from aurora.email.inbox import Inbox
    from aurora.tool_plugins.gmail import service
    return Inbox(service()[1], tuple(settings.GMAIL_WORK_DOMAINS))
