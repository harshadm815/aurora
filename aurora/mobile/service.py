"""
Everything the phone connects to, run together while "Phone" is on.

  - the local listener (127.0.0.1:MOBILE_PORT): USB cable and Tailscale
  - the USB link (usb.py): ``adb reverse`` for a plugged-in phone
  - the home Wi-Fi listener (HTTPS on MOBILE_WIFI_PORT) and the discovery
    responder, only while "Wi-Fi" is on **and** the PC is connected to a
    network you marked as home; checked every MOBILE_WIFI_CHECK_SECONDS,
    so leaving home closes them and coming back opens them again.
"""

import logging
import threading

from aurora.config import settings
from aurora.mobile import network, tls
from aurora.mobile.devices import DeviceStore
from aurora.mobile.events import EventHub
from aurora.mobile.files import Outbox
from aurora.mobile.server import MobileServer
from aurora.mobile.signing import NonceCache
from aurora.mobile.speech import PhoneSpeech
from aurora.mobile import usb as usb_module
from aurora.mobile.usb import UsbLink

logger = logging.getLogger(__name__)


class MobileService:

    def __init__(self, bridge, store: DeviceStore | None = None, wifi: bool = False,
                 home_networks: list[str] | None = None, ssid=None, address=None, usb: UsbLink | None = None,
                 check_seconds: float | None = None, speech=None):
        self.bridge = bridge
        self.store = store or DeviceStore()
        self.nonces = NonceCache()
        self.cert, self.key = tls.paths(settings.MOBILE_DATABASE_PATH)
        tls.ensure_certificate(self.cert, self.key)
        self.fingerprint = tls.fingerprint(self.cert)
        self.events = EventHub()
        self.speech = speech if speech is not None else PhoneSpeech()
        self.outbox = Outbox(settings.MOBILE_MAX_FILE_BYTES)
        self.local = MobileServer(bridge, self.store, nonces=self.nonces, connection=self.connection,
                                  events=self.events, speech=self.speech, outbox=self.outbox)
        self.usb = usb if usb is not None else UsbLink(settings.MOBILE_PORT, usb_module.find_adb())
        self.wifi_enabled = wifi
        self.home_networks = list(home_networks or [])
        self._ssid = ssid or (lambda: network.current_ssid())          # looked up when called (tests replace them)
        self._address = address or (lambda: network.lan_address())
        self.check_seconds = settings.MOBILE_WIFI_CHECK_SECONDS if check_seconds is None else check_seconds
        self.network: str | None = None          # the Wi-Fi network the PC is on (last check)
        self.wifi: MobileServer | None = None
        self.wifi_problem = ""
        self._discovery = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle

    @property
    def address(self) -> str:
        return self.local.address

    @property
    def port(self) -> int:
        return self.local.port

    def start(self) -> None:
        self.local.start()                        # raises OSError if the port is taken
        self.usb.start()
        self._stop.clear()
        self.check_network()
        if self._watcher is None and self.check_seconds > 0:
            self._watcher = threading.Thread(target=self._watch, name="aurora-mobile-network", daemon=True)
            self._watcher.start()

    def stop(self) -> None:
        self._stop.set()
        if self._watcher is not None:
            self._watcher.join(timeout=3)
            self._watcher = None
        self._stop_wifi()
        self.usb.stop()
        self.local.stop()

    def _watch(self):
        while not self._stop.wait(self.check_seconds):
            try:
                self.check_network()
                self.speech.release_if_idle()
            except Exception:
                logger.exception("Mobile: network check failed")

    # ------------------------------------------------------------ Wi-Fi

    def set_wifi(self, enabled: bool, home_networks: list[str]) -> None:
        with self._lock:
            self.wifi_enabled = enabled
            self.home_networks = list(home_networks)
        self.check_network()

    @property
    def at_home(self) -> bool:
        return self.network is not None and self.network in self.home_networks

    def check_network(self) -> None:
        """Opens or closes the Wi-Fi listener to match the network the PC is on now."""
        with self._lock:
            self.network = self._ssid()
            address = self._address() if self.wifi_enabled and self.at_home else None
            if address is None:
                self._stop_wifi()
                return
            if self.wifi is not None and self.wifi.host == address:
                return
            self._stop_wifi()
            server = MobileServer(self.bridge, self.store, port=settings.MOBILE_WIFI_PORT, host=address,
                                  kind="wifi", tls=tls.server_context(self.cert, self.key), nonces=self.nonces,
                                  connection=self.connection, events=self.events, speech=self.speech,
                                  outbox=self.outbox)
            server.call_lock = self.local.call_lock
            try:
                server.start()
                self._discovery = network.DiscoveryResponder(address, settings.MOBILE_DISCOVERY_PORT,
                                                             server.port, self.fingerprint)
            except OSError as error:
                server.stop()
                self.wifi_problem = f"Couldn't listen on Wi-Fi: {error.strerror or error}"
                logger.warning("Mobile: Wi-Fi listener couldn't start (%s)", type(error).__name__)
                return
            self.wifi, self.wifi_problem = server, ""
            logger.info("Mobile: listening on the home Wi-Fi")

    def _stop_wifi(self) -> None:
        if self._discovery is not None:
            self._discovery.stop()
            self._discovery = None
        if self.wifi is not None:
            self.wifi.stop()
            self.wifi = None
            logger.info("Mobile: stopped listening on Wi-Fi")

    # ------------------------------------------------------------ for the phone and Settings

    def connection(self) -> dict:
        """What the phone is told: the certificate to pin and, while at home, the Wi-Fi address."""
        wifi = self.wifi
        return {"fingerprint": self.fingerprint, "wifi_port": settings.MOBILE_WIFI_PORT,
                "discovery_port": settings.MOBILE_DISCOVERY_PORT,
                "wifi_address": wifi.host if wifi is not None else None}

    def call_phone(self) -> None:
        """Rings the paired phone (it calls back over its own connection)."""
        self.events.publish("call", reason="pc")
        self.speech.warm()                          # ready by the time you answer

    def send_file(self, path) -> str:
        """Offers a file you chose to the phone, which fetches it at once. Returns its name; raises FileError."""
        key, offer = self.outbox.offer(path)
        self.events.publish("file", file=key, name=offer.name, size=offer.size)
        return offer.name

    def describe(self) -> dict:
        wifi = self.wifi
        return {
            "usb": "no adb" if not self.usb.available else ("phone plugged in" if self.usb.phones else "no phone"),
            "network": self.network,
            "at_home": self.at_home,
            "wifi": f"{wifi.host}:{wifi.port}" if wifi is not None else None,
            "wifi_problem": self.wifi_problem,
        }
