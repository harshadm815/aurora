"""
The Mobile Companion's web server: the phone app and its API.

Safety, in order:
  - it listens on 127.0.0.1 only, never on your Wi-Fi or the internet;
    Tailscale (on your PC and phone) forwards it to the phone with HTTPS
  - requests must name this PC (localhost) or its Tailscale name (*.ts.net)
    in their Host header, so a web page can't reach it by DNS tricks
  - every API call except pairing needs a paired device's key
  - approving actions ("yes" to "Shall I turn on the light?") needs the
    device's "allow actions" permission; memories need "allow memories"
  - bodies are limited to 64 KB; nothing a user says is logged, only
    which device used which endpoint (devices.py's access log)

API (JSON):
    POST /api/pair       {"code", "name"}  → {"token", "device"}
    GET  /api/status                       → AI state, model, CPU/RAM, version
    GET  /api/history                      → the shared conversation (last 40 messages)
    POST /api/chat       {"text"}          → {"reply", "needs_confirmation", "sources"}
    GET  /api/today                        → {"briefing", "reminders"}
    GET  /api/memories                     → saved facts (if allowed)
    POST /api/unpair                       → this device disconnects itself
"""

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aurora.config import settings
from aurora.mobile.devices import DeviceStore, PairingError

logger = logging.getLogger(__name__)

WEB = Path(__file__).resolve().parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
MAX_BODY = 64 * 1024
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


def host_allowed(host: str | None) -> bool:
    """Only this PC (localhost) and Tailscale names reach the server."""
    name = (host or "").strip().lower()
    if name.startswith("["):                               # [::1]:8765
        name = name[1:].split("]", 1)[0]
    else:
        name = name.rsplit(":", 1)[0] if name.count(":") == 1 else name
    return name in ("localhost", "127.0.0.1", "::1") or any(
        name.endswith(suffix) and len(name) > len(suffix) for suffix in settings.MOBILE_HOST_SUFFIXES)


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "AURORA"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing

    def log_message(self, format, *args):          # no request lines (paths could be personal) in the log
        pass

    def _send(self, status: HTTPStatus, body: bytes, content_type: str, cache: bool = False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if cache else "no-store")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: HTTPStatus, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
        if length > MAX_BODY:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "That's too long.")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "Bad request.")
        return data

    def _device(self):
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else None
        device = self.server.store.authenticate(token)
        if device is None:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "This phone isn't paired (or was disconnected). Pair it again.")
        return device

    def _guard(self) -> bool:
        if not host_allowed(self.headers.get("Host")):
            self._send(HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain")
            return False
        return True

    # --------------------------------------------------------------- routes

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?", 1)[0]
        if path in STATIC:
            name, kind = STATIC[path]
            self._send(HTTPStatus.OK, (WEB / name).read_bytes(), kind, cache=True)
            return
        self._api("GET", path)

    def do_POST(self):
        if not self._guard():
            return
        self._api("POST", self.path.split("?", 1)[0])

    def _api(self, method: str, path: str):
        store, bridge = self.server.store, self.server.bridge
        device = None
        try:
            if (method, path) == ("POST", "/api/pair"):
                data = self._body()
                try:
                    token, device = store.pair(str(data.get("code", "")), str(data.get("name", "")))
                except PairingError as error:
                    store.log("(unpaired)", "pair", "refused")
                    raise ApiError(HTTPStatus.FORBIDDEN, str(error))
                self._json(HTTPStatus.OK, {"token": token, "device": device.name})
                return
            if not path.startswith("/api/"):
                raise ApiError(HTTPStatus.NOT_FOUND, "Not found.")
            device = self._device()
            what = path[5:]
            if (method, path) == ("GET", "/api/status"):
                result = bridge.status()
            elif (method, path) == ("GET", "/api/history"):
                result = {"messages": bridge.history(40)}
            elif (method, path) == ("POST", "/api/chat"):
                text = " ".join(str(self._body().get("text", "")).split())[:4000]
                if not text:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "Type a message first.")
                if bridge.pending() and not device.allow_actions:
                    raise ApiError(HTTPStatus.FORBIDDEN, "AURORA is waiting for an answer to an action. This phone "
                                                         "isn't allowed to approve actions, so answer it on the PC.")
                result = bridge.chat(text, device.name)
            elif (method, path) == ("GET", "/api/today"):
                result = {"briefing": bridge.briefing(), "reminders": bridge.reminders()}
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


class MobileServer:
    """``start()`` serves on 127.0.0.1:port in a background thread; ``stop()`` ends it."""

    def __init__(self, bridge, store: DeviceStore | None = None, port: int | None = None):
        self.bridge = bridge
        self.store = store or DeviceStore()
        self.port = settings.MOBILE_PORT if port is None else port
        self._http: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._http is not None

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        if self._http is not None:
            return
        http = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)     # this PC only
        http.daemon_threads = True
        http.store, http.bridge = self.store, self.bridge
        self.port = http.server_address[1]
        self._http = http
        self._thread = threading.Thread(target=http.serve_forever, name="aurora-mobile", daemon=True)
        self._thread.start()
        logger.info("Mobile: server on 127.0.0.1:%d", self.port)

    def stop(self) -> None:
        if self._http is None:
            return
        self._http.shutdown()
        self._http.server_close()
        self._http = None
        logger.info("Mobile: server stopped")
