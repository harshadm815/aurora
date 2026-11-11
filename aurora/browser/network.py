"""
The AURORA Browser and the network gate.

Chromium (inside Qt WebEngine) makes its own connections, not through
AURORA's httpx clients, so a request interceptor in the browser page asks
``BrowserNetwork.decide`` about every request a page makes: the page
itself, and every image, script, font and tracker it loads from other
sites.

- The gate's rules apply (aurora/network/gate.py): internet on / ask /
  off, and the local-network switch. The browser may open any site, since
  you choose the sites (``"any_host"`` in NETWORK_SERVICES["Browser"]).
- With internet access on "ask", nothing loads until you say yes once per
  session; the Browser page asks when you first open a page.
- Each host is added to System -> Network activity the first time it is
  contacted (and the first time it is blocked) in a session, not once per
  image, so the list stays readable.
- For each page, the sites it contacted are listed in the Browser page
  ("Sites this page contacted"), so other sites a page loads from are
  visible.

Addresses that never leave this PC (data:, blob:, about:, Qt's own qrc:
and the built-in PDF viewer's chrome-extension: pages) are always
allowed and not listed.
"""

import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from aurora.network.gate import THIS_PC, gate as default_gate

COMPONENT = "Browser"
LOCAL_SCHEMES = {"data", "blob", "about", "qrc", "chrome-extension", "chrome", "devtools"}   # never file:
NETWORK_SCHEMES = {"http", "https", "ws", "wss"}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    host: str = ""
    kind: str = THIS_PC
    reason: str = ""
    can_ask: bool = False


class BrowserNetwork:

    def __init__(self, gate=None, component: str = COMPONENT):
        self.gate = gate or default_gate
        self.component = component
        self._lock = threading.Lock()
        self._listed: set[tuple[str, bool]] = set()       # (host, allowed) already in the activity list
        self._contacted: dict[str, dict[str, bool]] = {}  # page host -> {host contacted: allowed}
        self.on_ask = None                                  # called (from any thread) when going online needs a yes

    def decide(self, url: str, page_url: str = "", method: str = "GET") -> Decision:
        """Called for every request. Thread-safe (the interceptor runs on Chromium's IO thread)."""
        try:
            parts = urlsplit(url)
        except ValueError:
            return Decision(False, reason="not a valid address")
        scheme = parts.scheme.lower()
        if scheme in LOCAL_SCHEMES:
            return Decision(True)
        if scheme not in NETWORK_SCHEMES:
            return Decision(False, reason=f"{scheme}: addresses aren't opened")
        host = (parts.hostname or "").lower()
        if not host:
            return Decision(False, reason="the address has no site in it")
        kind, reason, can_ask = self.gate.decide(self.component, host)
        allowed = not reason
        self._note(host, kind, method, allowed, reason, page_url)
        if can_ask and self.on_ask is not None:
            self.on_ask(host)
        return Decision(allowed, host, kind, reason, can_ask)

    def _note(self, host, kind, method, allowed, reason, page_url) -> None:
        page = (urlsplit(page_url).hostname or "").lower() if page_url else host
        with self._lock:
            self._contacted.setdefault(page or host, {})
            previous = self._contacted[page or host].get(host)
            self._contacted[page or host][host] = allowed if previous is None else (previous or allowed)
            first = (host, allowed) not in self._listed
            self._listed.add((host, allowed))
        if first:
            self.gate.record(self.component, host, kind, method, "sent" if allowed else "blocked", reason=reason)

    def contacted(self, page_url: str) -> list[tuple[str, bool]]:
        """(host, allowed) for every site the page at page_url contacted, the page's own site first."""
        page = (urlsplit(page_url).hostname or "").lower()
        with self._lock:
            hosts = dict(self._contacted.get(page, {}))
        return sorted(hosts.items(), key=lambda item: (item[0] != page, not _same_site(item[0], page), item[0]))

    def forget_page(self, page_url: str) -> None:
        """Starts the page's list again (a new page load from the same site)."""
        page = (urlsplit(page_url).hostname or "").lower()
        with self._lock:
            self._contacted.pop(page, None)

    def reset(self) -> None:
        with self._lock:
            self._listed.clear()
            self._contacted.clear()


def _same_site(host: str, page: str) -> bool:
    """bbc.co.uk and static.files.bbc.co.uk are the same site (roughly: shared last two labels)."""
    def base(name):
        labels = name.split(".")
        return ".".join(labels[-3:] if len(labels) > 2 and len(labels[-1]) == 2 and len(labels[-2]) <= 3
                        else labels[-2:])
    return bool(host and page) and base(host) == base(page)


def is_other_site(host: str, page_url: str) -> bool:
    page = (urlsplit(page_url).hostname or "").lower()
    return not _same_site(host, page)
