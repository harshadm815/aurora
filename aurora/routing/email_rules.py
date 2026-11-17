"""
Routing rule for the Gmail Assistant (the Gmail tool).

    Aurora, check my Gmail · any new emails? · show me today's important emails
    do I have any unanswered emails? · find the email about tomorrow's meeting
    summarise email 2 · summarise this conversation · disconnect Gmail

Only messages that clearly mean email: "mail" as in post, or "remind me to email Alex" (a reminder),
don't come here. Sending is not part of this rule (read-only for now).
"""

import re

from aurora.routing import decision as targets
from aurora.routing.decision import RouteDecision
from aurora.routing.intent import Intent

# Your mail, not email in general ("what is email?" is a question for the Brain).
_EMAIL = re.compile(r"\b(g-?mail|inbox|mailbox)\b|\be-?mail (report|digest|briefing)\b|\b(my|any|new|unread|today'?s|important|unanswered|latest|"
                    r"recent)\b[\w\s']{0,20}\be-?mails?\b|\bthe e-?mails? (about|from|regarding|on)\b", re.I)
_ASK = re.compile(r"\b(check|show|list|any|new|unread|important|unanswered|find|search|look|summari[sz]e|"
                  r"what('s| is| are)|do i have|read|open|explain|disconnect|sign out|revoke|today'?s)\b", re.I)
_FOLLOW_UP = re.compile(r"^\s*(?:please\s+)?(summari[sz]e|explain|read)\s+(?:e-?mail|mail|message|number|#)\s*\d{1,2}\b"
                        r"|^\s*(?:please\s+)?summari[sz]e (this|that|the) (conversation|thread|e-?mail)\b", re.I)
_NOT_THIS = re.compile(r"\b(remind me|every (day|morning|evening|week)|send|reply|forward|draft|write an? e-?mail)\b",
                       re.I)


def email_rule(original: str, body: str) -> RouteDecision | None:
    text = (body or original).strip()
    if _FOLLOW_UP.search(text):
        return RouteDecision(Intent.EMAIL, 0.9, targets.GMAIL, {"request": text},
                             "A follow-up about an email AURORA listed.")
    if not _EMAIL.search(text) or not _ASK.search(text) or _NOT_THIS.search(text):
        return None
    return RouteDecision(Intent.EMAIL, 0.92, targets.GMAIL, {"request": text},
                         "The message asks about the user's email.")
