"""
Mobile Companion (aurora/mobile/): pairing, device keys, permissions, the
server's security checks and API (a real server on 127.0.0.1, a free
port), and the bridge to AURORA. No network beyond this PC's loopback,
no microphone, no Tailscale.
"""

import http.client
import json
import socket
import sqlite3
import threading
import time
from datetime import datetime, timedelta

import pytest

# The real socket functions, kept before conftest's no_network guard replaces them, so these tests
# can reach their own server, and only that: 127.0.0.1 on the port it was given (never Ollama).
_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection
_ALLOWED_PORTS: set[int] = set()


@pytest.fixture(autouse=True)
def only_our_server(monkeypatch):
    def check(address):
        host, port = address[0], address[1]
        if host not in ("127.0.0.1", "localhost") or port not in _ALLOWED_PORTS:
            raise RuntimeError("Tests must not use the network; inject a fake client.")

    def connect(sock, address):
        check(address)
        return _REAL_CONNECT(sock, address)

    def create_connection(address, *args, **kwargs):
        check(address)
        return _REAL_CREATE(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    yield
    _ALLOWED_PORTS.clear()

from aurora.config import settings
from aurora.mobile import devices as devices_module
from aurora.mobile.devices import DeviceStore, PairingError
from aurora.mobile.server import MobileServer, host_allowed


# --- Pairing and devices -----------------------------------------------------------------

def test_pairing_code_works_once(tmp_path):
    store = DeviceStore(tmp_path / "m.db")
    code = store.new_code()
    assert len(code) == 8 and set(code) <= set(devices_module.CODE_ALPHABET)
    token, device = store.pair(code.lower()[:4] + " " + code[4:].lower(), "  Pixel   7 ")   # spaces and case don't matter
    assert device.name == "Pixel 7" and len(token) >= 40
    with pytest.raises(PairingError):
        store.pair(code, "Second phone")                                        # one use only
    assert store.authenticate(token).name == "Pixel 7"
    assert store.authenticate("not-a-key") is None and store.authenticate(None) is None


def test_only_a_hash_of_the_key_is_stored(tmp_path):
    store = DeviceStore(tmp_path / "m.db")
    token, _ = store.pair(store.new_code(), "Phone")
    with sqlite3.connect(tmp_path / "m.db") as db:
        dump = "\n".join(db.iterdump())
    assert token not in dump


def test_wrong_codes_withdraw_the_code(tmp_path):
    store = DeviceStore(tmp_path / "m.db")
    code = store.new_code()
    for _ in range(devices_module.MAX_WRONG_CODES - 1):
        with pytest.raises(PairingError, match="isn't right"):
            store.pair("AAAAAAAA", "x")
    with pytest.raises(PairingError, match="Too many"):
        store.pair("AAAAAAAA", "x")
    with pytest.raises(PairingError):
        store.pair(code, "x")                                                   # even the right one now


def test_codes_expire(tmp_path):
    now = [datetime(2026, 11, 14, 9, 0)]
    store = DeviceStore(tmp_path / "m.db", clock=lambda: now[0])
    code = store.new_code()
    now[0] += timedelta(minutes=devices_module.CODE_MINUTES, seconds=1)
    assert not store.code_active
    with pytest.raises(PairingError, match="no pairing code"):
        store.pair(code, "x")


def test_permissions_disconnect_and_log(tmp_path):
    store = DeviceStore(tmp_path / "m.db")
    token, device = store.pair(store.new_code(), "Phone")
    assert not device.allow_memories and not device.allow_actions               # off until you allow them
    store.set_permissions(device.id, memories=True)
    assert store.authenticate(token).allow_memories and store.devices()[0].last_seen is not None
    store.remove(device.id)
    assert store.authenticate(token) is None and store.devices() == []
    assert [(d, w, o) for _, d, w, o in store.recent_log()] == [("Phone", "pair", "allowed")]


@pytest.mark.parametrize("host,ok", [("127.0.0.1:8765", True), ("localhost", True), ("[::1]:8765", True),
                                     ("my-pc.tail1234.ts.net", True), ("evil.example.com", False),
                                     ("ts.net", False), ("127.0.0.1.evil.com", False), ("", False),
                                     ("my-pc.ts.net.evil.com", False)])
def test_host_check(host, ok):
    assert host_allowed(host) is ok


# --- The server ----------------------------------------------------------------------------

class FakeBridge:
    def __init__(self):
        self.waiting = False
        self.said = []

    def chat(self, text, device):
        self.said.append((device, text))
        return {"reply": f"echo: {text}", "needs_confirmation": False, "sources": ""}

    def pending(self):
        return self.waiting

    def status(self):
        return {"ai": "idle", "model": "tinyllama", "cpu": 12.0, "ram": 80.0, "version": settings.VERSION}

    def history(self, limit):
        return [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]

    def briefing(self):
        return "Here's your briefing."

    def reminders(self):
        return [{"text": "call home", "due": "2026-11-14T18:00"}]

    def memories(self):
        return [{"value": "likes tea", "category": "Preference"}]


@pytest.fixture
def server(tmp_path):
    bridge = FakeBridge()
    server = MobileServer(bridge, DeviceStore(tmp_path / "m.db"), port=0)
    server.start()
    _ALLOWED_PORTS.add(server.port)
    server.fake = bridge
    yield server
    server.stop()


def call(server, method, path, body=None, token=None, host=None, raw=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{server.port}", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    try:
        return response.status, json.loads(payload), dict(response.getheaders())
    except ValueError:
        return response.status, payload, dict(response.getheaders())


def _paired(server, name="Pixel"):
    code = server.store.new_code()
    status, data, _ = call(server, "POST", "/api/pair", {"code": code, "name": name})
    assert status == 200
    return data["token"]


def test_server_listens_on_this_pc_only(server):
    assert server._http.server_address[0] == "127.0.0.1"


def test_app_is_served_with_security_headers(server):
    status, page, headers = call(server, "GET", "/")
    assert status == 200 and b"Pair this phone" in page
    assert "default-src 'self'" in headers["Content-Security-Policy"] and headers["X-Frame-Options"] == "DENY"
    for path in ("/app.js", "/app.css", "/manifest.webmanifest", "/sw.js", "/icon.svg"):
        assert call(server, "GET", path)[0] == 200
    assert call(server, "GET", "/../settings.py")[0] == 404
    assert call(server, "GET", "/aurora_memory.db")[0] == 404


def test_other_host_names_are_refused(server):
    assert call(server, "GET", "/", host="attacker.example")[0] == 403
    assert call(server, "POST", "/api/pair", {"code": "x"}, host="attacker.example")[0] == 403


def test_api_needs_a_paired_key(server):
    for method, path in (("GET", "/api/status"), ("GET", "/api/history"), ("POST", "/api/chat"),
                         ("GET", "/api/today"), ("GET", "/api/memories")):
        status, data, _ = call(server, method, path, {"text": "hi"} if method == "POST" else None)
        assert status == 401 and "isn't paired" in data["error"]
    assert call(server, "GET", "/api/status", token="made-up")[0] == 401
    assert server.fake.said == []


def test_pairing_over_the_api(server):
    status, data, _ = call(server, "POST", "/api/pair", {"code": "WRONGONE", "name": "x"})
    assert status == 403
    token = _paired(server)
    status, data, _ = call(server, "GET", "/api/status", token=token)
    assert status == 200 and data["model"] == "tinyllama"


def test_chat_today_history(server):
    token = _paired(server)
    status, data, _ = call(server, "POST", "/api/chat", {"text": "  what is 2 +   2 "}, token=token)
    assert status == 200 and data["reply"] == "echo: what is 2 + 2" and server.fake.said == [("Pixel", "what is 2 + 2")]
    assert call(server, "GET", "/api/history", token=token)[1]["messages"][1]["text"] == "hello"
    today = call(server, "GET", "/api/today", token=token)[1]
    assert today["briefing"].startswith("Here's") and today["reminders"][0]["text"] == "call home"
    assert call(server, "POST", "/api/chat", {"text": "   "}, token=token)[0] == 400


def test_memories_need_permission(server):
    token = _paired(server)
    status, data, _ = call(server, "GET", "/api/memories", token=token)
    assert status == 403 and "isn't allowed" in data["error"]
    server.store.set_permissions(server.store.devices()[0].id, memories=True)
    assert call(server, "GET", "/api/memories", token=token)[1]["memories"][0]["value"] == "likes tea"


def test_approving_actions_needs_permission(server):
    token = _paired(server)
    server.fake.waiting = True                       # AURORA asked "Shall I turn on the light?"
    status, data, _ = call(server, "POST", "/api/chat", {"text": "yes"}, token=token)
    assert status == 403 and "on the PC" in data["error"] and server.fake.said == []
    server.store.set_permissions(server.store.devices()[0].id, actions=True)
    assert call(server, "POST", "/api/chat", {"text": "yes"}, token=token)[0] == 200


def test_large_or_broken_bodies_are_refused(server):
    token = _paired(server)
    assert call(server, "POST", "/api/chat", token=token, raw=b"x" * (70 * 1024))[0] == 413
    assert call(server, "POST", "/api/chat", token=token, raw=b"{not json")[0] == 400
    assert call(server, "POST", "/api/chat", token=token, raw=b"[1, 2]")[0] == 400


def test_unpair_and_the_log_has_no_messages(server):
    token = _paired(server)
    call(server, "POST", "/api/chat", {"text": "my secret plans"}, token=token)
    assert call(server, "POST", "/api/unpair", token=token)[0] == 200
    assert call(server, "GET", "/api/status", token=token)[0] == 401
    log = server.store.recent_log()
    assert ("Pixel", "chat", "allowed") in [(d, w, o) for _, d, w, o in log]
    assert not any("secret" in w for _, _, w, _ in log)


# --- The bridge to AURORA, and switching it on --------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def backend(qapp, memory, tmp_path, monkeypatch):
    from aurora.brain.brain import Brain
    from aurora.config import preferences
    from aurora.interface.backend import Backend, wait_for
    from aurora.kernel.engine import AuroraEngine
    from conftest import FakeProvider

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    b = Backend(engine_factory=lambda: AuroraEngine(Brain(FakeProvider()), memory=memory))
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    yield b
    b.shutdown()


def _in_thread(qapp, function):
    """Runs ``function`` on another thread (like the server does) while the GUI thread keeps working."""
    result = {}
    thread = threading.Thread(target=lambda: result.update(value=function()))
    thread.start()
    end = time.time() + 20
    while thread.is_alive() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return result.get("value")


def test_bridge_uses_the_shared_conversation(backend, qapp):
    from aurora.interface.mobile_bridge import MobileBridge

    bridge = MobileBridge(backend)
    shown = []
    backend.remote_exchange.connect(lambda device, text, reply: shown.append((device, text, reply)))
    answer = _in_thread(qapp, lambda: bridge.chat("what is 12 times 7", "Pixel"))
    assert answer["reply"] == "12 * 7 = 84" and shown == [("Pixel", "what is 12 times 7", "12 * 7 = 84")]
    assert bridge.history(10)[-2:] == [{"role": "user", "text": "what is 12 times 7"},
                                       {"role": "assistant", "text": "12 * 7 = 84"}]
    assert bridge.status()["version"] == settings.VERSION and not bridge.pending()
    assert bridge.briefing().startswith("Here's your briefing")
    assert bridge.reminders() == [] and isinstance(bridge.memories(), list)


def test_switching_the_phone_server_on_and_off(backend):
    assert backend.mobile is None
    assert backend.set_mobile(True) == ""
    port = backend.mobile.port
    _ALLOWED_PORTS.add(port)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
    assert connection.getresponse().status == 200
    connection.close()
    backend.set_mobile(False)
    assert backend.mobile is None
    with pytest.raises(OSError):
        http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/")


def test_settings_switch_starts_the_server(qapp, backend, monkeypatch):
    from aurora.interface import settings_view

    monkeypatch.setattr(settings_view.SettingsView, "_apply_startup", lambda self: True)
    monkeypatch.setattr(settings_view.SettingsView, "_apply_wake", lambda self, changed=True: True)
    page = settings_view.SettingsView(backend)
    page.mobile_enabled.setChecked(True)
    page.auto_save()
    assert backend.mobile is not None and backend.preferences["mobile_enabled"] is True
    assert "Running on this PC only" in page.mobile_status.text() and page.pair_button.isEnabled()
    page.mobile_enabled.setChecked(False)
    page.auto_save()
    assert backend.mobile is None and "Off" in page.mobile_status.text()
