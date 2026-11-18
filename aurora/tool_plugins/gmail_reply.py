"""
Replying to an email (the EmailReply intent): drafts from your words, sends only after your yes.

    reply to email 2 saying that I will attend the meeting
    reply to Alex's email saying thanks, I'll send it tomorrow
    change the reply to: Sorry, I can't make it on Tuesday
    yes / yes, send it        sends that exact message      (anything else: nothing is sent)

Rules (Docs/COMMUNICATIONS_PLAN.md):
  - never sends on its own, never on silence: every email is one yes, to the full message shown
    (recipient, subject, text); a changed draft is a new message and needs a new yes
  - no attachments, ever, in this version
  - only with the "email.send" permission AND Google's send permission (Settings → Gmail → Allow
    replies…); text inside an email can't make AURORA send anything (the yes must come from you in Chat
    or on the phone with "Approve actions")
  - every draft, approval, send, refusal and failure is in the audit trail (email/audit.py)
"""

import re

from aurora.config import settings
from aurora.email import audit
from aurora.email.gmail import GmailError
from aurora.email.oauth import SEND, AuthError
from aurora.email.reply import Draft, draft, mime, with_text
from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

COMPONENT = "GmailReply"
_NUMBER = re.compile(r"\b(?:email|mail|message|number|#)\s*(\d{1,2})\b", re.I)
_CHANGE = re.compile(r"^\s*(?:please\s+)?(?:change|make|edit|rewrite)\s+(?:the|my|that)?\s*(?:reply|draft|it|message)"
                     r"\s+(?:to|so it says|to say)\s*:?\s*(.+)$", re.I | re.S)
_SAYING = re.compile(r"\b(?:saying|say|to say|telling (?:them|him|her)|that says|with)\b\s*:?\s*(.+)$", re.I | re.S)
_TARGET = re.compile(r"\breply (?:to|back to)?\s*(.*?)\s*(?:\bsaying\b|\bsay\b|\bto say\b|\btelling\b|\bthat says\b|\bwith\b|$)",
                     re.I | re.S)


class GmailReplyTool(BaseTool):
    required_permissions = (Permission.NETWORK, Permission.EMAIL_READ, Permission.EMAIL_SEND)

    def __init__(self, reader, signature: str | None = None):
        """[reader] is the Gmail tool (its inbox and the emails it last listed)."""
        super().__init__(COMPONENT)
        self.reader = reader
        self.signature = settings.GMAIL_SIGNATURE if signature is None else signature
        self.last: Draft | None = None            # the latest draft, for "change the reply to …"

    def execute(self, request: str = "", **arguments) -> ToolResult:
        text = (request or "").strip()
        try:
            auth = self.reader.auth()
            if auth is not None and SEND not in auth.scopes:
                return ToolResult(False, "AURORA isn't allowed to send email yet. On the PC: Settings → Gmail → "
                                         "Allow replies… (Google asks you to approve sending).")
            change = _CHANGE.match(text)
            if change:
                if self.last is None:
                    return ToolResult(False, "There's no reply to change. Say \"reply to email 1 saying …\" first.")
                return self._offer(with_text(self.last, change.group(1), self.signature))
            original = self._original(text)
            if original is None:
                return ToolResult(False, "Which email? Say \"check my Gmail\" or \"find the email about …\", then "
                                         "\"reply to email 1 saying …\".")
            saying = _SAYING.search(text)
            if not saying:
                return ToolResult(False, "What should the reply say? For example: \"reply to email 1 saying I'll be "
                                         "there.\"")
            inbox = self.reader.inbox()
            return self._offer(draft(inbox.client.message(original.id, full=True), saying.group(1), inbox.me(),
                                     self.signature))
        except ValueError as error:
            return ToolResult(False, str(error))
        except (GmailError, AuthError) as error:
            return ToolResult(False, str(error), retryable=getattr(error, "retryable", False))

    def _original(self, text: str):
        """The email to reply to: "email 2" in the last list, "this" (the only/first one), or found by name."""
        shown = self.reader.shown
        number = _NUMBER.search(text)
        if number:
            n = int(number.group(1))
            return shown[n - 1].email if 1 <= n <= len(shown) else None
        target = (_TARGET.search(text).group(1) if _TARGET.search(text) else "").strip()
        if re.fullmatch(r"(?i)(this|that|the|it)?\s*(e-?mail|mail|message|one)?", target or "") and shown:
            return shown[0].email
        if target:
            found = self.reader.inbox().find(target, limit=3)
            if found:
                self.reader.shown = found
                return found[0].email
        return None

    def _offer(self, item: Draft) -> ToolResult:
        self.last = item
        audit.record("drafted", to=item.to, fingerprint=item.fingerprint, thread=item.thread_id)

        def send() -> ToolResult:
            return self._send(item)

        message = (f"Here's the reply. Nothing is sent until you say yes.\n\n{item.preview()}\n\n"
                   f"Send this email? (yes / no) — or say \"change the reply to: …\"")
        return ToolResult(True, message, data={"fingerprint": item.fingerprint}, confirm=send)

    def _send(self, item: Draft) -> ToolResult:
        """Runs only after your yes to exactly [item] (the engine's confirmation)."""
        audit.record("approved", to=item.to, fingerprint=item.fingerprint)
        if self.last is not None and self.last.fingerprint != item.fingerprint:
            audit.record("refused", to=item.to, fingerprint=item.fingerprint, why="a newer draft exists")
            return ToolResult(False, "That draft was replaced by a newer one, so it wasn't sent. Say yes to the "
                                     "newest draft instead.")
        inbox = self.reader.inbox()
        try:
            sent_id = inbox.client.send(mime(item, inbox.me()), item.thread_id)
        except (GmailError, AuthError) as error:
            audit.record("failed", to=item.to, fingerprint=item.fingerprint, why=type(error).__name__)
            return ToolResult(False, f"The reply wasn't sent: {error}", retryable=getattr(error, "retryable", False))
        audit.record("sent", to=item.to, fingerprint=item.fingerprint, gmail_id=sent_id)
        self.last = None
        return ToolResult(True, f"Sent to {item.to}. Gmail accepted it (id {sent_id}); it's in your Sent mail. "
                                f"AURORA can't tell whether it has been read.")

    def declined(self) -> None:
        if self.last is not None:
            audit.record("declined", to=self.last.to, fingerprint=self.last.fingerprint)
