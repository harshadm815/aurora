"""
Mobile Companion (aurora/mobile/): pairing (USB only, one phone, a key held
by the phone), signed requests, the server's security checks and API (real
servers on 127.0.0.1, free ports, HTTPS for the Wi-Fi listener), the home
Wi-Fi switch, the USB link (fake adb) and the bridge to AURORA. No network
beyond this PC's loopback, no microphone, no adb, no netsh.
"""

import base64
import hashlib
import http.client
import json
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

# The real socket functions, kept before conftest's no_network guard replaces them, so these tests
# can reach their own servers, and only those: 127.0.0.1 on the ports they were given (never Ollama).
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

from aurora.config import preferences, settings
from aurora.mobile import devices as devices_module, signing, tls
from aurora.mobile.devices import DeviceStore, PairingError
from aurora.mobile.network import DISCOVERY_QUESTION, parse_ssid
from aurora.mobile.server import MobileServer, host_allowed
from aurora.mobile.service import MobileService
from aurora.mobile.usb import UsbLink, parse_devices


class Phone:
    """Stands in for the AURORA app: an ECDSA P-256 key (on a real phone it lives in the Keystore)."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.public = base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()

    def sign(self, method, path, body=b"", when=None, nonce=None):
        when = str(int(time.time() if when is None else when))
        nonce = nonce or secrets.token_urlsafe(12)
        signature = self.key.sign(signing.canonical(method, path, when, nonce, body), ec.ECDSA(hashes.SHA256()))
        return {"X-Aurora-Time": when, "X-Aurora-Nonce": nonce,
                "X-Aurora-Signature": base64.b64encode(signature).decode()}


@pytest.fixture
def phone():
    return Phone()


# --- Pairing and devices -----------------------------------------------------------------

def test_pairing_code_works_once(tmp_path, phone):
    store = DeviceStore(tmp_path / "m.db")
    code = store.new_code()
    assert len(code) == 8 and set(code) <= set(devices_module.CODE_ALPHABET)
    token, device = store.pair(code.lower()[:4] + " " + code[4:].lower(), "  Pixel   7 ", phone.public)
    assert device.name == "Pixel 7" and len(token) >= 40 and device.public_key == phone.public
    assert store.authenticate(token).name == "Pixel 7"
    assert store.authenticate("not-a-key") is None and store.authenticate(None) is None


def test_only_one_phone_can_be_paired(tmp_path, phone):
    store = DeviceStore(tmp_path / "m.db")
    code = store.new_code()
    store.pair(code, "Mine", phone.public)
    assert store.full
    with pytest.raises(PairingError, match="already paired"):
        store.new_code()                                                        # no code while paired
    with pytest.raises(PairingError):
        store.pair(code, "Second phone", Phone().public)
    store.remove(store.devices()[0].id)
    assert not store.full and store.new_code()                                  # disconnect, then pair again


def test_pairing_needs_a_phone_key(tmp_path):
    store = DeviceStore(tmp_path / "m.db")
    code = store.new_code()
    for bad in ("", "not base64!", base64.b64encode(b"garbage").decode()):
        with pytest.raises(PairingError, match="key"):
            store.pair(code, "Phone", bad)
    rsa_like = base64.b64encode(ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
    with pytest.raises(PairingError, match="P-256"):
        store.pair(code, "Phone", rsa_like)


def test_phones_without_a_key_are_disconnected(tmp_path):
    with sqlite3.connect(tmp_path / "m.db") as db:                              # a 0.45 database (web app)
        db.execute("""CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                      token_hash TEXT NOT NULL UNIQUE, created TEXT NOT NULL, last_seen TEXT,
                      allow_memories INTEGER NOT NULL DEFAULT 0, allow_actions INTEGER NOT NULL DEFAULT 0)""")
        db.execute("INSERT INTO devices (name, token_hash, created) VALUES ('Old', 'abc', '2026-11-14T09:00')")
    assert DeviceStore(tmp_path / "m.db").devices() == []


def test_only_a_hash_of_the_key_is_stored(tmp_path, phone):
    store = DeviceStore(tmp_path / "m.db")
    token, _ = store.pair(store.new_code(), "Phone", phone.public)
    with sqlite3.connect(tmp_path / "m.db") as db:
        dump = "\n".join(db.iterdump())
    assert token not in dump


def test_wrong_codes_withdraw_the_code(tmp_path, phone):
    store = DeviceStore(tmp_path / "m.db")
    code = store.new_code()
    for _ in range(devices_module.MAX_WRONG_CODES - 1):
        with pytest.raises(PairingError, match="isn't right"):
            store.pair("AAAAAAAA", "x", phone.public)
    with pytest.raises(PairingError, match="Too many"):
        store.pair("AAAAAAAA", "x", phone.public)
    with pytest.raises(PairingError):
        store.pair(code, "x", phone.public)                                     # even the right one now


def test_codes_expire(tmp_path, phone):
    now = [datetime(2026, 11, 14, 9, 0)]
    store = DeviceStore(tmp_path / "m.db", clock=lambda: now[0])
    code = store.new_code()
    now[0] += timedelta(minutes=devices_module.CODE_MINUTES, seconds=1)
    assert not store.code_active
    with pytest.raises(PairingError, match="no pairing code"):
        store.pair(code, "x", phone.public)


def test_permissions_disconnect_and_log(tmp_path, phone):
    store = DeviceStore(tmp_path / "m.db")
    token, device = store.pair(store.new_code(), "Phone", phone.public)
    assert not device.allow_memories and not device.allow_actions               # off until you allow them
    store.set_permissions(device.id, memories=True)
    assert store.authenticate(token).allow_memories and store.devices()[0].last_seen is not None
    store.remove(device.id)
    assert store.authenticate(token) is None and store.devices() == []
    assert [(d, w, o) for _, d, w, o in store.recent_log()] == [("Phone", "pair", "allowed")]


# --- Signatures ----------------------------------------------------------------------------

def _verify(phone_key, headers, method="POST", path="/api/chat", body=b'{"text":"hi"}', nonces=None, clock=time.time):
    signing.verify(phone_key, method, path, headers.get("X-Aurora-Time"), headers.get("X-Aurora-Nonce"),
                   headers.get("X-Aurora-Signature"), body, nonces or signing.NonceCache(), clock=clock)


def test_a_signed_request_is_accepted_once(phone):
    nonces = signing.NonceCache()
    headers = phone.sign("POST", "/api/chat", b'{"text":"hi"}')
    _verify(phone.public, headers, nonces=nonces)
    with pytest.raises(signing.SignatureError, match="already used"):
        _verify(phone.public, headers, nonces=nonces)                          # replayed


def test_signatures_from_another_key_or_for_other_content_fail(phone):
    headers = phone.sign("POST", "/api/chat", b'{"text":"hi"}')
    with pytest.raises(signing.SignatureError, match="paired phone"):
        _verify(Phone().public, headers)                                        # someone else's phone
    with pytest.raises(signing.SignatureError, match="paired phone"):
        _verify(phone.public, headers, body=b'{"text":"delete everything"}')    # changed on the way
    with pytest.raises(signing.SignatureError, match="paired phone"):
        _verify(phone.public, headers, path="/api/memories")


def test_old_or_unsigned_requests_fail(phone):
    old = phone.sign("POST", "/api/chat", b'{"text":"hi"}', when=time.time() - 600)
    with pytest.raises(signing.SignatureError, match="clock"):
        _verify(phone.public, old)
    with pytest.raises(signing.SignatureError, match="isn't signed"):
        _verify(phone.public, {})
    bad_nonce = phone.sign("POST", "/api/chat", b'{"text":"hi"}', nonce="x y")
    with pytest.raises(signing.SignatureError):
        _verify(phone.public, bad_nonce)


# --- Host check ------------------------------------------------------------------------------

@pytest.mark.parametrize("host,ok", [("127.0.0.1:8765", True), ("localhost", True), ("[::1]:8765", True),
                                     ("my-pc.tail1234.ts.net", True), ("evil.example.com", False),
                                     ("ts.net", False), ("127.0.0.1.evil.com", False), ("", False),
                                     ("my-pc.ts.net.evil.com", False), ("192.168.1.20:8766", False)])
def test_host_check(host, ok):
    assert host_allowed(host) is ok


def test_the_wifi_address_is_allowed_on_its_listener():
    assert host_allowed("192.168.1.20:8766", ("192.168.1.20",))
    assert not host_allowed("192.168.1.21:8766", ("192.168.1.20",))


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

    def earlier_reminders(self):
        return [{"text": "test the phone", "due": "2026-11-14T14:49"}]

    def memories(self):
        return [{"value": "likes tea", "category": "Preference"}]


@pytest.fixture
def server(tmp_path):
    bridge = FakeBridge()
    server = MobileServer(bridge, DeviceStore(tmp_path / "m.db"), port=0,
                          connection=lambda: {"fingerprint": "ab" * 32, "wifi_port": 8766, "wifi_address": None})
    server.start()
    _ALLOWED_PORTS.add(server.port)
    server.fake = bridge
    yield server
    server.stop()


def call(server, method, path, body=None, token=None, host=None, raw=None, phone=None, connection=None,
         headers=None):
    connection = connection or http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    sent = {"Host": host or f"127.0.0.1:{server.port}", "Content-Type": "application/json"}
    if token:
        sent["Authorization"] = f"Bearer {token}"
    if phone is not None:
        sent.update(phone.sign(method, path, data if method == "POST" else b""))
    sent.update(headers or {})
    connection.request(method, path, body=data if method == "POST" else None, headers=sent)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    try:
        return response.status, json.loads(payload), dict(response.getheaders())
    except ValueError:
        return response.status, payload, dict(response.getheaders())


def _paired(server, phone, name="Pixel"):
    code = server.store.new_code()
    status, data, _ = call(server, "POST", "/api/pair", {"code": code, "name": name, "public_key": phone.public})
    assert status == 200, data
    return data["token"]


def test_server_listens_on_this_pc_only(server):
    assert server._http.server_address[0] == "127.0.0.1"


def test_no_web_app_any_more(server):
    status, page, headers = call(server, "GET", "/")
    assert status == 200 and b"AURORA app" in page
    assert "default-src 'none'" in headers["Content-Security-Policy"] and headers["X-Frame-Options"] == "DENY"
    for path in ("/app.js", "/sw.js", "/../settings.py", "/aurora_memory.db"):
        assert call(server, "GET", path)[0] == 404


def test_other_host_names_are_refused(server, phone):
    assert call(server, "GET", "/", host="attacker.example")[0] == 403
    assert call(server, "POST", "/api/pair", {"code": "x"}, host="attacker.example")[0] == 403


def test_pairing_only_over_usb(server, phone):
    code = server.store.new_code()
    body = {"code": code, "name": "x", "public_key": phone.public}
    status, data, _ = call(server, "POST", "/api/pair", body, host="my-pc.tail1234.ts.net")     # via Tailscale
    assert status == 403 and "USB" in data["error"]
    status, data, _ = call(server, "POST", "/api/pair", body)
    assert status == 200 and data["fingerprint"] == "ab" * 32 and data["wifi_port"] == 8766


def test_api_needs_the_key_and_the_phones_signature(server, phone):
    for method, path in (("GET", "/api/status"), ("GET", "/api/history"), ("POST", "/api/chat"),
                         ("GET", "/api/today"), ("GET", "/api/memories")):
        status, data, _ = call(server, method, path, {"text": "hi"} if method == "POST" else None)
        assert status == 401 and "isn't paired" in data["error"]
    token = _paired(server, phone)
    status, data, _ = call(server, "GET", "/api/status", token=token)                      # copied key, no signature
    assert status == 401 and "isn't signed" in data["error"]
    status, data, _ = call(server, "GET", "/api/status", token=token, phone=Phone())       # another device's signature
    assert status == 401 and "paired phone" in data["error"]
    assert call(server, "GET", "/api/status", token="made-up", phone=phone)[0] == 401
    assert call(server, "GET", "/api/status", token=token, phone=phone)[1]["model"] == "tinyllama"
    assert server.fake.said == []
    assert any(o.startswith("refused: not signed") for _, _, _, o in server.store.recent_log())


def test_a_recorded_request_cant_be_replayed(server, phone):
    token = _paired(server, phone)
    headers = phone.sign("POST", "/api/chat", json.dumps({"text": "hi"}).encode())
    assert call(server, "POST", "/api/chat", {"text": "hi"}, token=token, headers=headers)[0] == 200
    status, data, _ = call(server, "POST", "/api/chat", {"text": "hi"}, token=token, headers=headers)
    assert status == 401 and "already used" in data["error"] and len(server.fake.said) == 1


def test_chat_today_history(server, phone):
    token = _paired(server, phone)
    status, data, _ = call(server, "POST", "/api/chat", {"text": "  what is 2 +   2 "}, token=token, phone=phone)
    assert status == 200 and data["reply"] == "echo: what is 2 + 2" and server.fake.said == [("Pixel", "what is 2 + 2")]
    assert call(server, "GET", "/api/history", token=token, phone=phone)[1]["messages"][1]["text"] == "hello"
    today = call(server, "GET", "/api/today", token=token, phone=phone)[1]
    assert today["briefing"].startswith("Here's") and today["reminders"][0]["text"] == "call home"
    assert today["earlier"][0]["text"] == "test the phone"
    assert call(server, "POST", "/api/chat", {"text": "   "}, token=token, phone=phone)[0] == 400
    assert call(server, "GET", "/api/connection", token=token, phone=phone)[1]["wifi_port"] == 8766


def test_memories_need_permission(server, phone):
    token = _paired(server, phone)
    status, data, _ = call(server, "GET", "/api/memories", token=token, phone=phone)
    assert status == 403 and "isn't allowed" in data["error"]
    server.store.set_permissions(server.store.devices()[0].id, memories=True)
    assert call(server, "GET", "/api/memories", token=token, phone=phone)[1]["memories"][0]["value"] == "likes tea"


def test_approving_actions_needs_permission(server, phone):
    token = _paired(server, phone)
    server.fake.waiting = True                       # AURORA asked "Shall I turn on the light?"
    status, data, _ = call(server, "POST", "/api/chat", {"text": "yes"}, token=token, phone=phone)
    assert status == 403 and "on the PC" in data["error"] and server.fake.said == []
    server.store.set_permissions(server.store.devices()[0].id, actions=True)
    assert call(server, "POST", "/api/chat", {"text": "yes"}, token=token, phone=phone)[0] == 200


def test_large_or_broken_bodies_are_refused(server, phone):
    token = _paired(server, phone)
    assert call(server, "POST", "/api/chat", token=token, raw=b"x" * (70 * 1024))[0] == 413
    assert call(server, "POST", "/api/chat", token=token, raw=b"{not json", phone=phone)[0] == 400
    assert call(server, "POST", "/api/chat", token=token, raw=b"[1, 2]", phone=phone)[0] == 400


def test_unpair_and_the_log_has_no_messages(server, phone):
    token = _paired(server, phone)
    call(server, "POST", "/api/chat", {"text": "my secret plans"}, token=token, phone=phone)
    assert call(server, "POST", "/api/unpair", token=token, phone=phone)[0] == 200
    assert call(server, "GET", "/api/status", token=token, phone=phone)[0] == 401
    log = server.store.recent_log()
    assert ("Pixel", "chat", "allowed") in [(d, w, o) for _, d, w, o in log]
    assert not any("secret" in w for _, _, w, _ in log)


# --- HTTPS for the Wi-Fi listener ------------------------------------------------------------

def _pinned(port, fingerprint):
    """Connects like the app: any certificate is offered, only the pinned one is accepted."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=context)
    connection.connect()
    seen = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
    assert seen == fingerprint
    return connection


def test_wifi_listener_uses_https_with_the_pinned_certificate(tmp_path, phone):
    cert, key = tls.paths(tmp_path / "m.db")
    tls.ensure_certificate(cert, key)
    fingerprint = tls.fingerprint(cert)
    tls.ensure_certificate(cert, key)
    assert tls.fingerprint(cert) == fingerprint                                 # made once, never replaced
    store = DeviceStore(tmp_path / "m.db")
    token, _ = store.pair(store.new_code(), "Pixel", phone.public)
    wifi = MobileServer(FakeBridge(), store, port=0, kind="wifi", tls=tls.server_context(cert, key))
    wifi.start()
    _ALLOWED_PORTS.add(wifi.port)
    try:
        status, data, _ = call(wifi, "GET", "/api/status", token=token, phone=phone,
                               connection=_pinned(wifi.port, fingerprint))
        assert status == 200 and data["model"] == "tinyllama"
        store.cancel_code()
        status, data, _ = call(wifi, "POST", "/api/pair", {"code": "x", "public_key": phone.public},
                               host="localhost", connection=_pinned(wifi.port, fingerprint))
        assert status == 403 and "USB" in data["error"]                         # never pair over Wi-Fi
        plain = http.client.HTTPConnection("127.0.0.1", wifi.port, timeout=5)
        with pytest.raises((http.client.HTTPException, OSError)):
            plain.request("GET", "/api/status", headers={"Host": "127.0.0.1"})
            plain.getresponse()
    finally:
        wifi.stop()


# --- Home Wi-Fi switching, discovery, USB ----------------------------------------------------

class FakeUsb:
    available, phones = False, []

    def start(self):
        pass

    def stop(self):
        pass


def test_wifi_listens_only_at_home(tmp_path):
    now_on = ["Cafe"]
    service = MobileService(FakeBridge(), DeviceStore(tmp_path / "m.db"), wifi=True, home_networks=["Home"],
                            ssid=lambda: now_on[0], address=lambda: "127.0.0.1", usb=FakeUsb(), check_seconds=0)
    service.start()
    _ALLOWED_PORTS.add(service.port)
    try:
        assert service.wifi is None and service.describe()["network"] == "Cafe" and not service.at_home
        now_on[0] = "Home"
        service.check_network()
        assert service.wifi is not None and service.wifi.tls is not None
        assert service.connection()["wifi_address"] == "127.0.0.1"
        assert service.connection()["fingerprint"] == service.fingerprint
        answer = _discover(service._discovery)
        assert answer["port"] == service.wifi.port and service.fingerprint.startswith(answer["fp"])
        service.set_wifi(False, ["Home"])
        assert service.wifi is None                                             # switched off
        service.set_wifi(True, ["Home"])
        now_on[0] = None
        service.check_network()
        assert service.wifi is None and service.connection()["wifi_address"] is None   # left home
    finally:
        service.stop()


def _discover(responder):
    port = responder._socket.getsockname()[1]
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.settimeout(5)
    try:
        probe.sendto(DISCOVERY_QUESTION, ("127.0.0.1", port))
        return json.loads(probe.recvfrom(256)[0])
    finally:
        probe.close()


def test_parse_ssid():
    connected = "    Name                   : Wi-Fi\n    State                  : connected\n" \
                "    SSID                   : Home Net 5G\n    BSSID                  : aa:bb:cc:dd:ee:ff\n"
    assert parse_ssid(connected) == "Home Net 5G"
    assert parse_ssid(connected.replace(": connected", ": disconnected")) is None
    assert parse_ssid("") is None


def test_usb_link_reverses_each_new_phone():
    calls = []

    def run(command):
        calls.append(command[1:])
        if command[1:] == ["devices"]:
            return "List of devices attached\n7049e596\tdevice\nAAAA\tunauthorized\n\n"
        return "8765\n"

    link = UsbLink(8765, adb="adb.exe", run=run)
    assert link.check() == ["7049e596"]
    assert ["-s", "7049e596", "reverse", "tcp:8765", "tcp:8765"] in calls
    calls.clear()
    link.check()
    assert calls == [["devices"]]                                               # already linked: nothing more
    assert parse_devices("List of devices attached\n") == []
    assert UsbLink(8765, adb=None).check() == [] and not UsbLink(8765, adb=None).available


def test_home_networks_preference_is_checked():
    assert preferences._valid("mobile_home_networks", ["Home", "OPPO A5 2020"])
    assert not preferences._valid("mobile_home_networks", "Home")
    assert not preferences._valid("mobile_home_networks", [""])
    assert not preferences._valid("mobile_home_networks", ["x"] * 6)
    assert preferences.defaults()["mobile_home_networks"] == [] and preferences.defaults()["mobile_wifi"] is False


# --- The bridge to AURORA, and switching it on --------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def backend(qapp, memory, tmp_path, monkeypatch):
    from aurora.brain.brain import Brain
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
    assert backend.mobile.wifi is None                                          # Wi-Fi off by default
    backend.set_mobile(False)
    assert backend.mobile is None
    with pytest.raises(OSError):
        http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/")


def test_settings_switch_starts_the_server(qapp, backend, monkeypatch, phone):
    from aurora.interface import settings_view

    monkeypatch.setattr(settings_view.SettingsView, "_apply_startup", lambda self: True)
    monkeypatch.setattr(settings_view.SettingsView, "_apply_wake", lambda self, changed=True: True)
    page = settings_view.SettingsView(backend)
    page.mobile_enabled.setChecked(True)
    page.auto_save()
    assert backend.mobile is not None and backend.preferences["mobile_enabled"] is True
    assert "On." in page.mobile_status.text() and "Wi-Fi: off" in page.mobile_status.text()
    page._refresh_phone()
    assert page.pair_button.isEnabled()
    backend.mobile.store.pair(backend.mobile.store.new_code(), "Mine", phone.public)
    page._refresh_phone()
    assert not page.pair_button.isEnabled()                                     # one phone only
    page.mobile_wifi.setChecked(True)
    page.auto_save()
    assert backend.preferences["mobile_wifi"] is True and backend.mobile.wifi_enabled
    assert "isn't on Wi-Fi" in page.mobile_status.text()
    page.mobile_enabled.setChecked(False)
    page.auto_save()
    assert backend.mobile is None and "Off" in page.mobile_status.text()


# --- The event feed (milestone 2) -------------------------------------------------------------

from aurora.mobile.events import EventHub


def test_event_hub_order_waiting_and_restart():
    hub = EventHub(keep=3)
    assert hub.since(-1) == {"session": hub.session, "last": 0, "events": []}
    started = time.time()
    assert hub.since(0, wait=0.2)["events"] == [] and time.time() - started >= 0.15      # waited, nothing new
    for n in range(4):
        hub.publish("reminder", text=f"r{n}")
    got = hub.since(1)
    assert [e["text"] for e in got["events"]] == ["r1", "r2", "r3"] and got["last"] == 4    # only the last 3 kept
    assert hub.since(-1)["events"] == [] and hub.since(-1)["last"] == 4                    # "where are we?" only
    assert [e["id"] for e in hub.since(99)["events"]] == [2, 3, 4]                        # ids from an older run
    assert [e["id"] for e in hub.since(3, session="old-run")["events"]] == [2, 3, 4]      # AURORA restarted
    assert [e["id"] for e in hub.since(3, session=hub.session)["events"]] == [4]
    threading.Timer(0.2, lambda: hub.publish("message", source="pc", text="hi", reply="hello")).start()
    started = time.time()
    woke = hub.since(4, wait=10)
    assert woke["events"][0]["reply"] == "hello" and time.time() - started < 5            # answered at once
    hub.close()
    assert hub.since(5, wait=10)["events"] == []                                          # closing frees waiters


def test_events_over_the_api_are_signed_with_the_query(server, phone):
    token = _paired(server, phone)
    status, data, _ = call(server, "GET", "/api/events?after=-1", token=token, phone=phone)
    assert status == 200 and data["last"] == 0
    server.events.publish("reminder", text="call home")
    data = call(server, "GET", "/api/events?after=0&wait=5", token=token, phone=phone)[1]
    assert data["events"][0]["kind"] == "reminder" and data["events"][0]["text"] == "call home"
    signed_for_other = phone.sign("GET", "/api/events?after=-1")
    status, data, _ = call(server, "GET", "/api/events?after=0", token=token, headers=signed_for_other)
    assert status == 401                                                                   # query changed on the way
    assert call(server, "GET", "/api/events?after=x", token=token, phone=phone)[0] == 400
    assert call(server, "GET", "/api/events?after=0", token=token)[0] == 401              # unsigned


def test_the_phone_hears_about_pc_chats_and_reminders(backend, qapp):
    from aurora.interface.backend import wait_for

    assert backend.set_mobile(True) == ""
    _ALLOWED_PORTS.add(backend.mobile.port)
    try:
        replies = []
        backend.send_message("what is 12 times 7", lambda result: replies.append(result), lambda error: None)
        assert wait_for(lambda: bool(replies), 10)
        last = backend.mobile.events.since(0)["events"][-1]
        assert (last["kind"], last["source"], last["text"], last["reply"]) == \
            ("message", "pc", "what is 12 times 7", "12 * 7 = 84")
        backend.reminder_due.emit({"id": 1, "text": "call home"})
        last = backend.mobile.events.since(0)["events"][-1]
        assert (last["kind"], last["text"]) == ("reminder", "call home")
    finally:
        backend.set_mobile(False)
    backend.reminder_due.emit({"id": 2, "text": "after"})                                  # disconnected: no error