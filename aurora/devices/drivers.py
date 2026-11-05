"""
How AURORA talks to devices.

- ``SimulatedDriver``: keeps state in memory. Used until real hardware
  exists; every answer says the device is simulated.
- ``HttpRelayDriver``: an ESP32 running firmware/esp32_device_controller,
  on the local network. Every request carries a secret token, so other
  devices on the Wi-Fi can't switch anything.
"""

import httpx

from aurora.config import settings
from aurora.devices.device import Command, Device, DeviceError, DeviceState


class Driver:
    simulated = False

    def state(self, device: Device) -> DeviceState:
        raise NotImplementedError

    def apply(self, device: Device, command: Command) -> DeviceState:
        raise NotImplementedError


class SimulatedDriver(Driver):
    simulated = True

    def __init__(self):
        self._states: dict[str, DeviceState] = {}

    def state(self, device):
        return self._states.setdefault(device.id, DeviceState())

    def apply(self, device, command):
        current = self.state(device)
        if command.action == "on":
            new = DeviceState(True, current.level or (device.range[1] if device.range else None))
        elif command.action == "off":
            new = DeviceState(False, current.level)
        elif command.action == "toggle":
            new = DeviceState(not current.on, current.level or (device.range[1] if device.range else None))
        else:  # set
            new = DeviceState(True, command.value)
        self._states[device.id] = new
        return new


class HttpRelayDriver(Driver):

    def __init__(self, url: str, token: str, client: httpx.Client | None = None):
        if not url.startswith(("http://", "https://")):
            raise ValueError("Device controller URL must start with http:// or https://")
        if not token:
            raise ValueError("The device controller needs DEVICE_CONTROLLER_TOKEN (see firmware README).")
        self.url = url.rstrip("/")
        self.token = token
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=settings.SENSOR_TIMEOUT_SECONDS)
        return self._client

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            response = self.client.request(
                method, f"{self.url}{path}", json=body, headers={"X-Aurora-Token": self.token},
            )
            if response.status_code == 401:
                raise DeviceError("The device controller rejected AURORA's token.")
            response.raise_for_status()
            data = response.json()
        except DeviceError:
            raise
        except httpx.HTTPError:
            raise DeviceError("I can't reach the device controller. Is it powered and on the Wi-Fi?") from None
        except ValueError:
            raise DeviceError("The device controller sent data I couldn't read.") from None
        if not isinstance(data, dict):
            raise DeviceError("The device controller sent data in an unexpected format.")
        return data

    @staticmethod
    def _state(data: dict) -> DeviceState:
        on = data.get("on")
        level = data.get("level")
        if not isinstance(on, bool) or (level is not None and not isinstance(level, int)):
            raise DeviceError("The device controller sent an invalid device state.")
        return DeviceState(on, level)

    def state(self, device):
        return self._state(self._request("GET", f"/devices/{device.id}"))

    def apply(self, device, command):
        body = {"command": command.action}
        if command.value is not None:
            body["value"] = command.value
        return self._state(self._request("POST", f"/devices/{device.id}", body))
