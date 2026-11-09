"""
The network gate: every HTTP request AURORA makes goes through here.

Each component that may use the network (Weather, Wikipedia, arXiv, the
sensor hub, the device controller) gets its HTTP client from
``gate.client(component)``. The client's transport asks the gate before
every request, including each redirect, and records the outcome.

Rules, from settings (and the Settings page):

1. A component may contact only its own destinations: the hosts listed in
   ``NETWORK_SERVICES`` (Weather → the two Open-Meteo hosts, …), or the
   address you configured for it (sensor and device URLs). Anything else
   is blocked, so a redirect can't carry a request elsewhere.
2. Internet destinations follow ``INTERNET_ACCESS``:
   - "off": nothing goes to the internet;
   - "ask" (default): the first request from each service in a session is
     held back and AURORA asks you, saying where it would go and what
     would be sent; after "yes", that service may go online until AURORA
     restarts;
   - "on": allowed without asking.
3. Your own network (an ESP32 on your Wi-Fi) follows
   ``LOCAL_NETWORK_ACCESS``. This PC (localhost) is always allowed.

The activity record keeps the time, component, host, purpose and outcome
of each request, never the path or query (they contain what you asked
about). It lives in memory only, and the log file gets the same line.
"""

import ipaddress
import logging
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import httpx

from aurora.config import settings

logger = logging.getLogger(__name__)

INTERNET = "internet"
LOCAL = "local network"
THIS_PC = "this PC"
MODES = ("on", "ask", "off")
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")


def classify(host: str) -> str:
    """Whether a host is this PC, your own network, or the internet."""
    host = (host or "").strip("[]").lower().rstrip(".")
    if host in ("localhost", "") or host.endswith(".localhost"):
        return THIS_PC
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith(LOCAL_SUFFIXES):
            return LOCAL
        return INTERNET
    if address.is_loopback:
        return THIS_PC
    if address.is_private or address.is_link_local:
        return LOCAL
    return INTERNET


class NetworkBlocked(httpx.TransportError):
    """The gate refused a request. A TransportError, so every component's
    existing "couldn't reach" handling applies; the tool executor then
    replaces that message with the real reason (or a question)."""

    def __init__(self, component: str, host: str, kind: str, reason: str, can_ask: bool = False):
        self.component, self.host, self.kind, self.reason, self.can_ask = component, host, kind, reason, can_ask
        super().__init__(reason)


@dataclass(frozen=True)
class NetworkRecord:
    time: datetime
    component: str
    host: str
    kind: str              # INTERNET, LOCAL or THIS_PC
    purpose: str
    method: str
    outcome: str           # "sent", "blocked", "failed"
    status: int | None = None
    reason: str = ""

    def describe(self) -> str:
        detail = f"HTTP {self.status}" if self.status is not None else self.reason
        return f"{self.time:%H:%M:%S}  {self.component} -> {self.host} ({self.kind}): {self.outcome}" + (
            f", {detail}" if detail else "")


class _Local(threading.local):
    block: NetworkBlocked | None = None


class NetworkGate:

    def __init__(self, clock=datetime.now):
        self.clock = clock
        self._lock = threading.Lock()
        self._approved: set[str] = set()
        self._extra_hosts: dict[str, set[str]] = {}
        self._records: deque[NetworkRecord] = deque(maxlen=settings.NETWORK_LOG_SIZE)
        self._local = _Local()
        self.listeners: list = []          # called with each NetworkRecord

    # ------------------------------------------------------------- policy

    @property
    def mode(self) -> str:
        mode = getattr(settings, "INTERNET_ACCESS", "ask")
        return mode if mode in MODES else "off"

    @staticmethod
    def service(component: str) -> dict:
        return dict(settings.NETWORK_SERVICES.get(component, {}))

    def destinations(self, component: str) -> set[str]:
        hosts = {h.lower() for h in self.service(component).get("hosts", [])}
        with self._lock:
            return hosts | self._extra_hosts.get(component, set())

    def allow_host(self, component: str, url_or_host: str) -> None:
        """Adds a destination you configured (a sensor or device URL)."""
        host = httpx.URL(url_or_host).host if "://" in url_or_host else url_or_host
        with self._lock:
            self._extra_hosts.setdefault(component, set()).add(host.lower())

    def approve(self, component: str) -> None:
        """You said yes: this service may use the internet until AURORA restarts."""
        with self._lock:
            self._approved.add(component)
        logger.info("Internet access approved for %s (this session)", component)

    def approved(self) -> list[str]:
        with self._lock:
            return sorted(self._approved)

    def forget_approvals(self) -> None:
        with self._lock:
            self._approved.clear()
        logger.info("Internet approvals cleared")

    def check(self, component: str, url: httpx.URL, method: str = "GET") -> str:
        """Returns the destination's kind, or raises NetworkBlocked (and records it)."""
        host = (url.host or "").lower()
        kind = classify(host)
        name = self.service(component).get("name", component)
        reason, can_ask = "", False
        if host not in self.destinations(component):
            reason = f"{host} isn't one of {name}'s destinations"
        elif kind == LOCAL and not settings.LOCAL_NETWORK_ACCESS:
            reason = "access to your local network is switched off"
        elif kind == INTERNET and self.mode == "off":
            reason = "internet access is switched off"
        elif kind == INTERNET and self.mode == "ask" and component not in self.approved():
            reason, can_ask = "internet access for this service hasn't been approved yet", True
        if reason:
            block = NetworkBlocked(component, host, kind, reason, can_ask)
            self._local.block = block
            self._record(component, host, kind, method, "blocked", reason=reason)
            raise block
        return kind

    # ----------------------------------------------------------- clients

    def transport(self, component: str, inner: httpx.BaseTransport | None = None) -> "GatedTransport":
        return GatedTransport(self, component, inner or httpx.HTTPTransport())

    def client(self, component: str, transport: httpx.BaseTransport | None = None, **kwargs) -> httpx.Client:
        """An httpx.Client whose every request passes this gate."""
        return httpx.Client(transport=self.transport(component, transport), **kwargs)

    # ------------------------------------------------- blocks and records

    def clear_block(self) -> None:
        self._local.block = None

    def take_block(self) -> NetworkBlocked | None:
        """The last request this thread had blocked since clear_block()."""
        block, self._local.block = self._local.block, None
        return block

    def records(self) -> list[NetworkRecord]:
        with self._lock:
            return list(self._records)

    def clear_records(self) -> None:
        with self._lock:
            self._records.clear()

    def _record(self, component, host, kind, method, outcome, status=None, reason="") -> None:
        record = NetworkRecord(self.clock(), component, host, kind,
                               self.service(component).get("purpose", ""), method, outcome, status, reason)
        with self._lock:
            self._records.append(record)
        logger.info("Network: %s", record.describe())
        for listener in list(self.listeners):
            try:
                listener(record)
            except Exception:
                logger.exception("Network listener failed")


class GatedTransport(httpx.BaseTransport):

    def __init__(self, gate: NetworkGate, component: str, inner: httpx.BaseTransport):
        self.gate, self.component, self.inner = gate, component, inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        kind = self.gate.check(self.component, request.url, request.method)
        host = (request.url.host or "").lower()
        try:
            response = self.inner.handle_request(request)
        except Exception as error:
            self.gate._record(self.component, host, kind, request.method, "failed", reason=type(error).__name__)
            raise
        self.gate._record(self.component, host, kind, request.method, "sent", status=response.status_code)
        return response

    def close(self) -> None:
        self.inner.close()


# The one gate every AURORA component uses.
gate = NetworkGate()
