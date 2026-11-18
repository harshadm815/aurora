"""
Replies to an email, prepared from your words and sent only after your yes to that exact message.

    draft(original, instruction, me, signature)   a Draft: recipient, subject, the full text
    mime(draft, me)                                the email itself (plain text, no attachments)

The draft is made from what you said, not invented: "reply saying that I will attend the meeting"
becomes "Hi Alex,\\n\\nI will attend the meeting.\\n\\n" (and your signature, if you set one).
It's addressed to the sender (or their Reply-To when it's at the same domain; a Reply-To elsewhere is
a phishing sign, so it's shown and not used). A Draft never changes: changing the text makes a new
Draft, which needs a new yes.
"""

import base64
import hashlib
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, getaddresses

from aurora.email.classify import domain
from aurora.tools.untrusted import one_line

_LEAD = re.compile(r"^\s*(?:and\s+)?(?:say(?:ing)?|tell(?:ing)? (?:them|him|her)|to say|that says|with)\s+(?:that\s+)?",
                   re.I)


@dataclass(frozen=True)
class Draft:
    to: str
    to_name: str
    subject: str
    body: str
    thread_id: str
    in_reply_to: str
    references: str
    original_id: str
    note: str = ""                       # e.g. a Reply-To that wasn't used

    @property
    def fingerprint(self) -> str:
        """What exactly was approved: recipient, subject and text (any change gives a new one)."""
        return hashlib.sha256(f"{self.to}\n{self.subject}\n{self.body}".encode("utf-8")).hexdigest()[:16]

    def preview(self) -> str:
        lines = [f"To: {self.to_name} <{self.to}>" if self.to_name else f"To: {self.to}",
                 f"Subject: {self.subject}", "", self.body.rstrip()]
        if self.note:
            lines += ["", f"Note: {self.note}"]
        return "\n".join(lines)


def words(instruction: str) -> str:
    """Your words for the reply: "saying that I will attend" → "I will attend." """
    text = _LEAD.sub("", instruction.strip()).strip().strip("\"“”'")
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


def draft(original, instruction: str, me: str, signature: str = "") -> Draft:
    """A reply to [original] (an Email) saying what you asked, addressed safely."""
    body_words = words(instruction)
    if not body_words:
        raise ValueError("What should the reply say? For example: reply to email 1 saying I'll be there.")
    to, note = original.sender, ""
    reply_to = getaddresses([original.headers.get("Reply-To", "")])
    if reply_to and reply_to[0][1]:
        candidate = reply_to[0][1].lower()
        if domain(candidate) == domain(original.sender):
            to = candidate
        elif candidate != original.sender:
            note = (f"the email asks for replies to go to {candidate}, a different domain from the sender's; AURORA "
                    f"replies to the sender's own address instead")
    if to == me:
        raise ValueError("That email is from you, so there's no one to reply to.")
    first = (one_line(original.sender_name).split() or [""])[0].strip(",") if original.sender_name else ""
    greeting = f"Hi {first}," if first and first.isalpha() else "Hello,"
    subject = one_line(original.subject)
    subject = subject if re.match(r"(?i)^re:", subject) else f"Re: {subject}"
    body = f"{greeting}\n\n{body_words}\n" + (f"\n{signature.strip()}\n" if signature.strip() else "")
    message_id = original.headers.get("Message-ID", "")
    references = " ".join(x for x in (original.headers.get("References", ""), message_id) if x).strip()
    return Draft(to=to, to_name=one_line(original.sender_name), subject=subject, body=body,
                 thread_id=original.thread_id, in_reply_to=message_id, references=references,
                 original_id=original.id, note=note)


def with_text(previous: Draft, text: str, signature: str = "") -> Draft:
    """The same reply with different words (a new Draft, so a new approval)."""
    body_words = words(text)
    greeting = previous.body.split("\n", 1)[0]
    body = f"{greeting}\n\n{body_words}\n" + (f"\n{signature.strip()}\n" if signature.strip() else "")
    return Draft(**{**previous.__dict__, "body": body})


def mime(item: Draft, me: str) -> str:
    """The email as Gmail wants it: base64url of the message (plain text, no attachments)."""
    message = EmailMessage()
    message["From"] = me
    message["To"] = formataddr((item.to_name, item.to)) if item.to_name else item.to
    message["Subject"] = item.subject
    if item.in_reply_to:
        message["In-Reply-To"] = item.in_reply_to
        message["References"] = item.references or item.in_reply_to
    message.set_content(item.body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
