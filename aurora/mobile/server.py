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
    DNS tricks; anything a browser sends from a web page (an Origin or
    Sec-Fetch-Site header, which the app never sends) is refused outright
  - at most MAX_CONNECTIONS open connections per listener, so something
    on the home network can't tie AURORA up by opening thousands
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
    GET  /api/call?reason=R&text=T         → 101, then a spoken call (call.py); one at a time
    GET  /api/history                      → the shared conversation (last 40 messages)
    POST /api/chat       {"text"}          → {"reply", "needs_confirmation", "sources"}
    GET  /api/today                        → {"briefing", "reminders"}
    GET  /api/memories                     → saved facts (if allowed)
    POST /api/memories   {"value"}         → adds a note to AURORA's memory (if allowed)
    POST /api/memories/edit {"id", "value"} / POST /api/memories/delete {"id"}   (if allowed)
    POST /api/clear                        → clears the shared conversation (memories stay)
    POST /api/history/import {"text", "reply"} → an exchange the phone had offline joins the conversation
    GET  /api/email/report                 → the latest end-of-day email report (if this phone may see it)
    POST /api/control    {"action"}        → "pause", "start" or "stop_speaking" only
    (While AURORA is closed, the phone helper answers only status, connection and "start": launcher.py.)
    POST /api/unpair                       → this phone disconnects itself
    POST /api/file?name=N  (the file)      → {"saved", "folder"}: kept in "From phone", never overwriting
    GET  /api/file?id=I                    → a file you sent from the PC (files.py)
"""

import json
import logging
import ssl
import threading
from http import HTTPStatus
from pathlib import Path
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aurora.config import settings
from aurora.mobile.devices import DeviceStore, PairingError
from aurora.mobile.events import EventHub
from aurora.mobile.files import FileError, Outbox, receive
from aurora.mobile.signing import NonceCache, SignatureError, verify
from aurora.mobile.call import CallSession
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
MAX_CONNECTIONS = 16
# While AURORA is closed the phone helper (launcher.py) serves only these, and only "start" as a command.
CLOSED_ROUTES = {("GET", "/api/status"), ("GET", "/api/connection"), ("POST", "/api/control")}
CLOSED_MESSAGE = "AURORA is closed on your PC. Start it from the app (Home or Control)."           # the phone uses 2-3 at once (events, a request, a call)


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

    def _device(self, method: str, path: str, raw: bytes, signed_path: str, body_hash: str | None = None):
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else None
        device = self.server.store.authenticate(token)
        if device is None:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "This phone isn't paired (or was disconnected). Pair it again.")
        try:
            verify(device.public_key, method, signed_path, self.headers.get("X-Aurora-Time"),
                   self.headers.get("X-Aurora-Nonce"), self.headers.get("X-Aurora-Signature"), raw,
                   self.server.nonces, body_hash=body_hash)
        except SignatureError as error:
            self.server.store.log(device.name, path[5:], "refused: not signed by the phone")
            raise ApiError(HTTPStatus.UNAUTHORIZED, str(error))
        return device

    def _guard(self) -> bool:
        from_a_web_page = self.headers.get("Origin") is not None or self.headers.get("Sec-Fetch-Site") is not None
        if from_a_web_page or not host_allowed(self.headers.get("Host"), self.server.extra_hosts):
            self.close_connection = True
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
            closed = getattr(bridge, "closed", False)
            if path == "/api/file":                    # streamed: checked against the signed SHA-256 as it arrives
                if method == "POST":
                    claimed = self.headers.get("X-Aurora-Content-Sha256") or ""
                    device = self._device(method, path, b"", full, body_hash=claimed)
                else:
                    device = self._device(method, path, b"", full)
                if closed:
                    raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, CLOSED_MESSAGE)
                self._file(method, device, self._query(full))
                return
            raw = (self._raw_body(MAX_VOICE_BODY if path == "/api/voice" else MAX_BODY)
                   if method == "POST" else b"")
            if (method, path) == ("POST", "/api/pair"):
                if closed:
                    raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "Open AURORA on the PC to pair a phone.")
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
            if closed and (method, path) not in CLOSED_ROUTES:
                raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, CLOSED_MESSAGE)
            if (method, path) == ("GET", "/api/events"):
                query = self._query(full)
                try:
                    after, wait = int(query.get("after", "-1")), float(query.get("wait", "0"))
                except ValueError:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
                self._json(HTTPStatus.OK, server.events.since(after, wait, query.get("session")))
                return                                   # not logged: the phone asks every half minute
            if (method, path) == ("GET", "/api/call"):
                self._call(device, self._query(full))
                return
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
                result = {**bridge.status(),
                          "permissions": {"memories": device.allow_memories, "actions": device.allow_actions,
                                          "email": device.allow_email}}
            elif (method, path) == ("GET", "/api/connection"):
                result = server.connection()
            elif (method, path) == ("GET", "/api/history"):
                result = {"messages": bridge.history(40)}
            elif (method, path) in (("POST", "/api/chat"), ("POST", "/api/memories"), ("POST", "/api/memories/edit"),
                                    ("POST", "/api/memories/delete"), ("POST", "/api/history/import")) \
                    and self._already_applied(raw):
                result = {"ok": True, "duplicate": True, "reply": ""}
            elif (method, path) == ("POST", "/api/chat"):
                text = " ".join(str(self._parse(raw).get("text", "")).split())[:4000]
                if not text:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Type a message first.")
                if bridge.pending() and not device.allow_actions:
                    raise ApiError(HTTPStatus.FORBIDDEN, "AURORA is waiting for an answer to an action. This phone "
                                                         "isn't allowed to approve actions, so answer it on the PC.")
                result = bridge.chat(text, device.name)
                self._mark_applied(raw)
            elif (method, path) == ("GET", "/api/today"):
                result = {"briefing": bridge.briefing(), "reminders": bridge.reminders(),
                          "earlier": bridge.earlier_reminders()}
            elif (method, path) == ("GET", "/api/email/report"):
                if not device.allow_email:
                    raise ApiError(HTTPStatus.FORBIDDEN, "This phone isn't allowed to see your email report. "
                                                         "Allow \"Email report\" on the PC's Phone page.")
                result = {"report": bridge.email_report()}
            elif (method, path) == ("GET", "/api/memories"):
                if not device.allow_memories:
                    raise ApiError(HTTPStatus.FORBIDDEN, "This phone isn't allowed to see your memories. "
                                                         "Allow it on the PC's Phone page.")
                result = {"memories": bridge.memories()}
            elif path in ("/api/memories", "/api/memories/edit", "/api/memories/delete") and method == "POST":
                if not device.allow_memories:
                    raise ApiError(HTTPStatus.FORBIDDEN, "This phone isn't allowed to change your memories. "
                                                         "Allow \"Memories\" on the PC's Phone page.")
                data = self._parse(raw)
                value = " ".join(str(data.get("value", "")).split())[:500]
                try:
                    if path == "/api/memories":
                        if not value:
                            raise ApiError(HTTPStatus.BAD_REQUEST, "Type what AURORA should remember.")
                        done = bridge.add_memory(value)
                    else:
                        memory_id = int(data.get("id"))
                        changed_at = float(data["changed_at"]) if data.get("changed_at") is not None else None
                        if path.endswith("/edit"):
                            if not value:
                                raise ApiError(HTTPStatus.BAD_REQUEST, "A memory can't be empty.")
                            done = bridge.edit_memory(memory_id, value, changed_at)
                        else:
                            done = bridge.forget_memory(memory_id, changed_at)
                except (TypeError, ValueError):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
                except RuntimeError as error:
                    raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, str(error))
                if not done:
                    raise ApiError(HTTPStatus.NOT_FOUND, "That memory isn't there any more.")
                self._mark_applied(raw)
                result = {"ok": True, "kept_pc": done == "kept"}
            elif (method, path) == ("POST", "/api/history/import"):
                data = self._parse(raw)
                text = " ".join(str(data.get("text", "")).split())[:4000]
                reply = str(data.get("reply", "")).strip()[:8000]
                if not text or not reply:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
                try:
                    bridge.import_exchange(text, reply, device.name)
                except RuntimeError as error:
                    raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, str(error))
                self._mark_applied(raw)
                result = {"ok": True}
            elif (method, path) == ("POST", "/api/clear"):
                bridge.clear_conversation()
                result = {"ok": True}
            elif (method, path) == ("POST", "/api/control"):
                action = str(self._parse(raw).get("action", ""))
                if action not in ("pause", "start", "stop_speaking"):
                    raise ApiError(HTTPStatus.BAD_REQUEST, "The phone can only pause or start AURORA, or stop it speaking.")
                if closed and action != "start":
                    raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, CLOSED_MESSAGE)
                result = {"message": bridge.control(action)}
                what = f"control: {action}"
            elif (method, path) == ("POST", "/api/unpair"):
                store.remove(device.id)
                result = {"ok": True}
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
            store.log(device.name, what, "allowed")
            self._json(HTTPStatus.OK, result)
        except ApiError as error:
            if path == "/api/file" and method == "POST":
                self.close_connection = True           # the unread file mustn't be taken for the next request
            if device is not None:
                store.log(device.name, path[5:], "refused")
            self._json(error.status, {"error": str(error)})
        except Exception:
            logger.exception("Mobile: request failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Something went wrong on the PC."})


    @staticmethod
    def _op(raw: bytes) -> str | None:
        """The id of a change the phone queued while the PC was away (letters, digits, - and _ only)."""
        try:
            op = json.loads(raw or b"{}").get("op")
        except (ValueError, AttributeError):
            return None
        if isinstance(op, str) and 8 <= len(op) <= 64 and op.replace("-", "").replace("_", "").isalnum():
            return op
        return None

    def _already_applied(self, raw: bytes) -> bool:
        op = self._op(raw)
        return op is not None and self.server.store.op_applied(op)

    def _mark_applied(self, raw: bytes) -> None:
        op = self._op(raw)
        if op is not None:
            self.server.store.mark_op(op)

    def _file(self, method: str, device, query: dict) -> None:
        server = self.server
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
            try:
                saved = receive(self.rfile, length, Path(server.files_dir), str(query.get("name", "")),
                                self.headers.get("X-Aurora-Content-Sha256", ""), server.max_file)
            except FileError as error:
                self.close_connection = True            # the rest of the body may still be on its way
                raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
            except OSError:
                self.close_connection = True
                logger.warning("Mobile: a file from the phone couldn't be saved")
                raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "The PC couldn't save that file.")
            server.store.log(device.name, "file", "received")   # never the file's name
            try:
                server.bridge.file_received(saved, device.name)
            except Exception:
                logger.exception("Mobile: couldn't announce a received file")
            self._json(HTTPStatus.OK, {"saved": saved.name, "folder": saved.parent.name})
            return
        offer = server.outbox.get(query.get("id", ""))
        if offer is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "That file is no longer offered. Send it again from the PC.")
        server.store.log(device.name, "file", "sent")
        left = offer.size
        with open(offer.path, "rb") as source:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(offer.size))
            self.send_header("X-Aurora-Name", quote(offer.name))
            self.send_header("Cache-Control", "no-store")
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            while left:
                chunk = source.read(min(256 * 1024, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        if left:
            self.close_connection = True                 # changed while sending: the phone sees it's short

    def _call(self, device, query: dict) -> None:
        server = self.server
        if server.speech is None:
            raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "Speech isn't available on the PC.")
        if not server.call_lock.acquire(blocking=False):
            raise ApiError(HTTPStatus.CONFLICT, "AURORA is already on a call.")
        try:
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "aurora-call")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            self.connection.settimeout(30)                # a phone that vanished ends the call
            server.store.log(device.name, "call", "allowed")
            session = CallSession(self.rfile, self.wfile, server.speech,
                                  chat=lambda text: server.bridge.chat(text, device.name),
                                  pending=server.bridge.pending, may_approve=device.allow_actions,
                                  reason=str(query.get("reason", ""))[:20], text=str(query.get("text", ""))[:300])
            ended = session.run()
            server.store.log(device.name, "call", f"ended: {ended}")
        finally:
            server.call_lock.release()


class _Http(ThreadingHTTPServer):
    daemon_threads = True
    tls: ssl.SSLContext | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):      # full: close at once instead of a new thread
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

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
                 speech=None, outbox: Outbox | None = None, files_dir=None):
        self.bridge = bridge
        self.outbox = outbox or Outbox(settings.MOBILE_MAX_FILE_BYTES)
        self.files_dir = files_dir
        self.store = store or DeviceStore()
        self.port = settings.MOBILE_PORT if port is None else port
        self.host = host
        self.kind = kind
        self.tls = tls
        self.nonces = nonces or NonceCache()
        self.connection = connection or (lambda: {})
        self.events = events or EventHub()
        self.speech = speech
        self.call_lock = threading.Lock()
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
        http.call_lock = self.call_lock
        http.outbox = self.outbox
        http.files_dir = self.files_dir or settings.MOBILE_FILES_DIR     # looked up now (tests change it)
        http.max_file = settings.MOBILE_MAX_FILE_BYTES
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
