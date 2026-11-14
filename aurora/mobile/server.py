"""
The Mobile Companion's server: the API the AURORA Android app uses.

Two listeners, the same API:
  - **local**: 127.0.0.1 only, plain HTTP. Reached over the USB cable
    (``adb reverse``, usb.py) or Tailscale (``tailscale serve``). The only
    place a phone can pair, and only with Host localhost (so not through
    Tailscale).
  - **wifi**: this PC's address on a network you marked as home, HTTPS with
    AURORA's own certificate, which the phone pins at pairing (tls.py).
    Started and stopped by service.py as you join and leave that network.

Safety, in order:
  - requests must name this PC in their Host header (localhost, the Wi-Fi
    address, or a Tailscale *.ts.net name), so web pages can't reach it by
    DNS tricks
  - every API call except pairing needs the phone's device key **and** a
    fresh signature from the key locked in that phone (signing.py)
  - only one phone can be paired (devices.py)
  - approving actions ("yes" to "Shall I turn on the light?") needs the
    phone's "allow actions" permission; memories need "allow memories"
  - bodies are limited to 64 KB; nothing a user says is logged, only
    which phone used which endpoint (devices.py's access log)

API (JSON):
    POST /api/pair       {"code", "name", "public_key"} → {"token", "device", "fingerprint", "wifi_port"}
    GET  /api/status                       → AI state, model, CPU/RAM, version
    GET  /api/connection                   → the Wi-Fi address while AURORA listens there
    GET  /api/events?after=N&wait=S        → new messages and due reminders (long polling; events.py)
    POST /api/voice      (16-bit mono WAV) → {"heard"}: what was said (speech.py; up to 60 s)
    POST /api/speak      {"text"}          → audio/wav in AURORA's voice
    GET  /api/history                      → the shared conversation (last 40 messages)
    POST /api/chat       {"text"}          → {"reply", "needs_confirmation", "sources"}
    GET  /api/today                        → {"briefing", "reminders"}
    GET  /api/memories                     → saved facts (if allowed)
    POST /api/unpair                       → this phone disconnects itself
"""

import json
import logging
import ssl
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aurora.config import settings
from aurora.mobile.devices import DeviceStore, PairingError
from aurora.mobile.events import EventHub
from aurora.mobile.signing import NonceCache, SignatureError, verify
from aurora.mobile.speech import SpeechError

logger = logging.getLogger(__name__)

MAX_BODY = 64 * 1024
MAX_VOICE_BODY = 2 * 1024 * 1024          # a minute of 16 kHz speech
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
LOCAL_NAMES = ("localhost", "127.0.0.1", "::1")


def host_name(host: str | None) -> str:
    name = (host or "").strip().lower()
    if name.startswith("["):                               # [::1]:8765
        return name[1:].split("]", 1)[0]
    return name.rsplit(":", 1)[0] if name.count(":") == 1 else name


def host_allowed(host: str | None, extra: tuple[str, ...] = ()) -> bool:
    """Only this PC (localhost, its Wi-Fi address) and Tailscale names reach the server."""
    name = host_name(host)
    return name in LOCAL_NAMES or name in extra or any(
        name.endswith(suffix) and len(name) > len(suffix) for suffix in settings.MOBILE_HOST_SUFFIXES)


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "AURORA"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30

    # ------------------------------------------------------------- plumbing

    def log_message(self, format, *args):          # no request lines (paths could be personal) in the log
        pass

    def _send(self, status: HTTPStatus, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _raw_body(self, limit: int = MAX_BODY) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
        if length < 0 or length > limit:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "That's too long.")
        return self.rfile.read(length) if length else b""

    @staticmethod
    def _parse(raw: bytes) -> dict:
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
        return data

    def _device(self, method: str, path: str, raw: bytes, signed_path: str):
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else None
        device = self.server.store.authenticate(token)
        if device is None:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "This phone isn't paired (or was disconnected). Pair it again.")
        try:
            verify(device.public_key, method, signed_path, self.headers.get("X-Aurora-Time"),
                   self.headers.get("X-Aurora-Nonce"), self.headers.get("X-Aurora-Signature"), raw,
                   self.server.nonces)
        except SignatureError as error:
            self.server.store.log(device.name, path[5:], "refused: not signed by the phone")
            raise ApiError(HTTPStatus.UNAUTHORIZED, str(error))
        return device

    def _guard(self) -> bool:
        if not host_allowed(self.headers.get("Host"), self.server.extra_hosts):
            self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            return False
        return True

    def _may_pair(self) -> bool:
        """Pairing only over the USB cable: the local listener, from this PC, addressed as localhost."""
        return (self.server.kind == "local" and self.client_address[0] in ("127.0.0.1", "::1")
                and host_name(self.headers.get("Host")) in LOCAL_NAMES)

    # --------------------------------------------------------------- routes

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(HTTPStatus.OK, b"AURORA. Use the AURORA app on your phone.", "text/plain; charset=utf-8")
            return
        self._api("GET", path, self.path)

    def do_POST(self):
        if not self._guard():
            return
        self._api("POST", self.path.split("?", 1)[0], self.path)

    @staticmethod
    def _query(full: str) -> dict:
        from urllib.parse import parse_qs, urlsplit
        return {key: values[-1] for key, values in parse_qs(urlsplit(full).query).items()}

    def _api(self, method: str, path: str, full: str | None = None):
        full = full or path                             # the signature covers the query too
        server = self.server
        store, bridge = server.store, server.bridge
        device = None
        try:
            raw = (self._raw_body(MAX_VOICE_BODY if path == "/api/voice" else MAX_BODY)
                   if method == "POST" else b"")
            if (method, path) == ("POST", "/api/pair"):
                if not self._may_pair():
                    store.log("(unpaired)", "pair", "refused: not over USB")
                    raise ApiError(HTTPStatus.FORBIDDEN, "Pairing works only with the phone plugged in by USB.")
                data = self._parse(raw)
                try:
                    token, device = store.pair(str(data.get("code", "")), str(data.get("name", "")),
                                               str(data.get("public_key", "")))
                except PairingError as error:
                    store.log("(unpaired)", "pair", "refused")
                    raise ApiError(HTTPStatus.FORBIDDEN, str(error))
                self._json(HTTPStatus.OK, {"token": token, "device": device.name, **server.connection()})
                return
            if not path.startswith("/api/"):
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
            device = self._device(method, path, raw, full)
            what = path[5:]
            if (method, path) == ("GET", "/api/events"):
                query = self._query(full)
                try:
                    after, wait = int(query.get("after", "-1")), float(query.get("wait", "0"))
                except ValueError:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
                self._json(HTTPStatus.OK, server.events.since(after, wait, query.get("session")))
                return                                   # not logged: the phone asks every half minute
            if (method, path) in (("POST", "/api/voice"), ("POST", "/api/speak")):
                if server.speech is None:
                    raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "Speech isn't available on the PC.")
                try:
                    if path == "/api/voice":
                        result = {"heard": server.speech.hear(raw)}
                    else:
                        text = str(self._parse(raw).get("text", ""))
                        audio = server.speech.say(text)
                        store.log(device.name, what, "allowed")
                        self._send(HTTPStatus.OK, audio, "audio/wav")
                        return
                except SpeechError as error:
                    raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
            elif (method, path) == ("GET", "/api/status"):
                result = bridge.status()
            elif (method, path) == ("GET", "/api/connection"):
                result = server.connection()
            elif (method, path) == ("GET", "/api/history"):
                result = {"messages": bridge.history(40)}
            elif (method, path) == ("POST", "/api/chat"):
                text = " ".join(str(self._parse(raw).get("text", "")).split())[:4000]
                if not text:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Type a message first.")
                if bridge.pending() and not device.allow_actions:
                    raise ApiError(HTTPStatus.FORBIDDEN, "AURORA is waiting for an answer to an action. This phone "
                                                         "isn't allowed to approve actions, so answer it on the PC.")
                result = bridge.chat(text, device.name)
            elif (method, path) == ("GET", "/api/today"):
                result = {"briefing": bridge.briefing(), "reminders": bridge.reminders(),
                          "earlier": bridge.earlier_reminders()}
            elif (method, path) == ("GET", "/api/memories"):
                if not device.allow_memories:
                    raise ApiError(HTTPStatus.FORBIDDEN, "This phone isn't allowed to see your memories. "
                                                         "Allow it in Settings → Phone on the PC.")
                result = {"memories": bridge.memories()}
            elif (method, path) == ("POST", "/api/unpair"):
                store.remove(device.id)
                result = {"ok": True}
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
            store.log(device.name, what, "allowed")
            self._json(HTTPStatus.OK, result)
        except ApiError as error:
            if device is not None:
                store.log(device.name, path[5:], "refused")
            self._json(error.status, {"error": str(error)})
        except Exception:
            logger.exception("Mobile: request failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Something went wrong on the PC."})


class _Http(ThreadingHTTPServer):
    daemon_threads = True
    tls: ssl.SSLContext | None = None

    def finish_request(self, request, client_address):
        if self.tls is not None:                      # TLS handshake on the request's own thread
            request.settimeout(15)
            try:
                request = self.tls.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError):
                return
        super().finish_request(request, client_address)


class MobileServer:
    """One listener. ``start()`` serves in a background thread; ``stop()`` ends it.

    ``kind`` is "local" (127.0.0.1, plain HTTP, pairing allowed) or "wifi"
    (``host``, HTTPS with ``tls``). ``connection`` returns what the phone is
    told about the Wi-Fi route (service.py supplies it)."""

    def __init__(self, bridge, store: DeviceStore | None = None, port: int | None = None,
                 host: str = "127.0.0.1", kind: str = "local", tls: ssl.SSLContext | None = None,
                 nonces: NonceCache | None = None, connection=None, events: EventHub | None = None,
                 speech=None):
        self.bridge = bridge
        self.store = store or DeviceStore()
        self.port = settings.MOBILE_PORT if port is None else port
        self.host = host
        self.kind = kind
        self.tls = tls
        self.nonces = nonces or NonceCache()
        self.connection = connection or (lambda: {})
        self.events = events or EventHub()
        self.speech = speech
        self._http: _Http | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._http is not None

    @property
    def address(self) -> str:
        return f"{'https' if self.tls else 'http'}://{self.host}:{self.port}/"

    def start(self) -> None:
        if self._http is not None:
            return
        http = _Http((self.host, self.port), Handler)
        http.tls = self.tls
        http.kind = self.kind
        http.store, http.bridge, http.nonces = self.store, self.bridge, self.nonces
        http.extra_hosts = () if self.host in ("127.0.0.1", "::1") else (self.host,)
        http.connection = self.connection
        http.events = self.events
        http.speech = self.speech
        self.port = http.server_address[1]
        self._http = http
        self._thread = threading.Thread(target=http.serve_forever, name=f"aurora-mobile-{self.kind}", daemon=True)
        self._thread.start()
        logger.info("Mobile: %s server started", self.kind)

    def stop(self) -> None:
        if self._http is None:
            return
        if self.kind == "local":
            self.events.close()                         # let waiting phones go
        self._http.shutdown()
        self._http.server_close()
        self._http = None
        logger.info("Mobile: %s server stopped", self.kind)
