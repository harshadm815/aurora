"""
The address bar: what you type becomes an address to open or a search.

    bbc.co.uk                   -> https://bbc.co.uk
    https://arxiv.org/abs/1     -> as typed
    localhost:8000              -> http://localhost:8000
    192.168.1.40/readings       -> http://192.168.1.40/readings
    black hole formation        -> the search engine (BROWSER_SEARCH_URL)

Only http and https pages are opened from the address bar. Anything else
(file:, javascript:, data:, chrome: ...) is refused, so typing or pasting
one can't read files on this PC or run a script.
"""

import ipaddress
import re
from urllib.parse import quote_plus, urlsplit, urlunsplit

from aurora.config import settings

WEB_SCHEMES = ("http", "https")
BLANK = "about:blank"

_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$", re.I)


class RefusedAddress(ValueError):
    """An address the browser won't open (not http or https)."""


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def _looks_like_address(text: str) -> bool:
    if " " in text:
        return False
    host = re.split(r"[/?#]", text, maxsplit=1)[0]
    host = re.sub(r":\d{1,5}$", "", host)
    return host.lower() == "localhost" or _is_ip(host) or bool(_HOST.match(host))


def search_url(words: str, template: str | None = None) -> str:
    template = template or settings.BROWSER_SEARCH_URL
    return template.replace("{query}", quote_plus(words.strip()))


def resolve(text: str, template: str | None = None) -> str:
    """The URL to open for what was typed. Raises RefusedAddress for other schemes."""
    text = (text or "").strip()
    if not text or text.lower() == BLANK:
        return BLANK
    match = _SCHEME.match(text)
    if match and not re.match(r"^[^:/]+:\d{1,5}(?:[/?#]|$)", text):     # "localhost:8000" isn't a scheme
        scheme = match.group(1).lower()
        if scheme not in WEB_SCHEMES:
            raise RefusedAddress(f"The browser only opens web pages (http and https), not {scheme}: addresses.")
        parts = urlsplit(text)
        if not parts.netloc:
            raise RefusedAddress("That address has no site in it.")
        return urlunsplit(parts)
    if _looks_like_address(text):
        host = re.split(r"[/?#:]", text, maxsplit=1)[0].lower()
        local = host == "localhost" or (_is_ip(host) and not ipaddress.ip_address(host).is_global)
        return ("http://" if local else "https://") + text
    return search_url(text, template)


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_web(url: str) -> bool:
    try:
        return urlsplit(url).scheme.lower() in WEB_SCHEMES
    except ValueError:
        return False


def without_fragment(url: str) -> str:
    """The same page without its #section part (one history entry per page)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
