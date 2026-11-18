"""
The Gmail API (read-only), called over HTTPS through AURORA's internet gate.

Only what the Gmail Assistant needs: your address, a list of messages for a search, one message (its
headers, labels, attachment names and plain text) and one conversation. Busy answers (429, 5xx) are
retried briefly; an expired access token is renewed once; everything else becomes a GmailError with a
message for you. Email contents are untrusted data.
"""

import base64
import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime

import httpx

from aurora.email.oauth import AuthError
from aurora.network import NetworkBlocked

logger = logging.getLogger(__name__)

API = "https://gmail.googleapis.com/gmail/v1/users/me"
KEEP_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Reply-To", "List-Unsubscribe", "List-Id",
                "Authentication-Results", "Return-Path", "Message-ID", "In-Reply-To", "References")
MAX_BODY = 20_000


class GmailError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Email:
    id: str
    thread_id: str
    sender_name: str
    sender: str                       # the address
    to: list[str]
    cc: list[str]
    subject: str
    date: datetime | None
    snippet: str
    labels: list[str]
    attachments: list[str] = field(default_factory=list)
    body: str = ""
    headers: dict = field(default_factory=dict)

    @property
    def unread(self) -> bool:
        return "UNREAD" in self.labels

    @property
    def who(self) -> str:
        return self.sender_name or self.sender


def _text(html_text: str) -> str:
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html_text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _walk(part: dict, plain: list, html_parts: list, attachments: list) -> None:
    mime = part.get("mimeType", "")
    name = part.get("filename") or ""
    body = part.get("body") or {}
    if name:
        attachments.append(name)
    elif mime == "text/plain" and body.get("data"):
        plain.append(_decode(body["data"]))
    elif mime == "text/html" and body.get("data"):
        html_parts.append(_decode(body["data"]))
    for child in part.get("parts") or []:
        _walk(child, plain, html_parts, attachments)


def parse(message: dict) -> Email:
    """A Gmail API message (format "full" or "metadata") as an Email."""
    payload = message.get("payload") or {}
    headers = {h.get("name", ""): h.get("value", "") for h in payload.get("headers") or []}
    headers = {k: v for k, v in headers.items() if k in KEEP_HEADERS}
    sender = getaddresses([headers.get("From", "")])
    name, address = sender[0] if sender else ("", "")
    try:
        date = parsedate_to_datetime(headers["Date"]) if headers.get("Date") else None
    except (TypeError, ValueError):
        date = None
    if date is None and message.get("internalDate"):
        date = datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc)
    plain, html_parts, attachments = [], [], []
    _walk(payload, plain, html_parts, attachments)
    body = "\n".join(plain).strip() or _text("\n".join(html_parts))
    return Email(
        id=message.get("id", ""), thread_id=message.get("threadId", ""),
        sender_name=name.strip(), sender=address.strip().lower(),
        to=[a.lower() for _, a in getaddresses([headers.get("To", "")]) if a],
        cc=[a.lower() for _, a in getaddresses([headers.get("Cc", "")]) if a],
        subject=headers.get("Subject", "").strip() or "(no subject)", date=date,
        snippet=html.unescape(message.get("snippet", "")), labels=list(message.get("labelIds") or []),
        attachments=attachments, body=body[:MAX_BODY], headers=headers)


class GmailClient:
    """Read-only Gmail. [auth] is a GoogleAuth; [http] an httpx client through the internet gate."""

    def __init__(self, auth, http: httpx.Client, sleep=time.sleep):
        self.auth = auth
        self.http = http
        self.sleep = sleep

    def _get(self, path: str, params: dict | None = None) -> dict:
        renewed = False
        for attempt in range(3):
            try:
                token = self.auth.access_token()
            except AuthError as error:
                raise GmailError(str(error))
            try:
                response = self.http.get(API + path, params=params or {}, timeout=30,
                                         headers={"Authorization": f"Bearer {token}"})
            except NetworkBlocked as error:
                raise GmailError(str(error))
            except httpx.HTTPError as error:
                raise GmailError(f"Couldn't reach Gmail ({type(error).__name__}). Check the internet connection.",
                                 retryable=True)
            if response.status_code == 200:
                return response.json()
            if response.status_code == 401 and not renewed:          # token no longer valid: renew once
                self.auth.expire_now()
                renewed = True
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                wait = min(float(response.headers.get("Retry-After", 2 ** attempt)), 10.0)
                self.sleep(wait)
                continue
            if response.status_code == 429:
                raise GmailError("Gmail asked AURORA to slow down. Try again in a minute.", retryable=True)
            if response.status_code == 403:
                raise GmailError("Gmail refused (no permission for that, or the Gmail API isn't enabled in your "
                                 "Google Cloud project).")
            if response.status_code == 404:
                raise GmailError("That email isn't there any more.")
            raise GmailError(f"Gmail answered with an error ({response.status_code}).", retryable=True)
        raise GmailError("Gmail kept refusing the request.", retryable=True)

    def address(self) -> str:
        return self._get("/profile").get("emailAddress", "")

    def search(self, query: str, limit: int = 25) -> list[str]:
        """Message ids for a Gmail search ("in:inbox newer_than:1d", "from:alex", …), newest first."""
        data = self._get("/messages", {"q": query, "maxResults": max(1, min(limit, 100))})
        return [m["id"] for m in data.get("messages") or []]

    def message(self, message_id: str, full: bool = False) -> Email:
        params = {"format": "full"} if full else {"format": "metadata", "metadataHeaders": list(KEEP_HEADERS)}
        return parse(self._get(f"/messages/{message_id}", params))

    def send(self, raw: str, thread_id: str = "") -> str:
        """Sends one email (base64url RFC 822, made by reply.mime). Returns Gmail's id for it.
        Called only by the reply tool after your yes to that exact message; never retried on its own."""
        try:
            token = self.auth.access_token()
        except AuthError as error:
            raise GmailError(str(error))
        body = {"raw": raw, **({"threadId": thread_id} if thread_id else {})}
        try:
            response = self.http.post(API + "/messages/send", json=body, timeout=30,
                                      headers={"Authorization": f"Bearer {token}"})
        except NetworkBlocked as error:
            raise GmailError(str(error))
        except httpx.HTTPError as error:
            raise GmailError(f"Couldn't reach Gmail ({type(error).__name__}); the email was NOT sent.", retryable=True)
        if response.status_code == 200:
            return response.json().get("id", "")
        if response.status_code == 403:
            raise GmailError("Gmail refused to send: AURORA doesn't have permission to send (Settings → Gmail → "
                             "Allow replies…). The email was NOT sent.")
        raise GmailError(f"Gmail refused to send ({response.status_code}); the email was NOT sent.",
                         retryable=response.status_code >= 500)

    def thread(self, thread_id: str) -> list[Email]:
        data = self._get(f"/threads/{thread_id}", {"format": "full"})
        return [parse(m) for m in data.get("messages") or []]
