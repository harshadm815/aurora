"""
Devices AURORA may control, and what each one allows.

Only devices listed in ``settings.DEVICES`` exist for AURORA. Each lists
the actions it allows and the limits on values, and whether every
change must be confirmed (the default).
"""

from dataclasses import dataclass, field

# What each kind of device can do, and the range of its setting.
KINDS = {
    "light": {"actions": {"on", "off", "toggle", "set"}, "setting": "brightness", "range": (1, 100), "unit": "%"},
    "fan": {"actions": {"on", "off", "toggle", "set"}, "setting": "speed", "range": (1, 3), "unit": ""},
    "plug": {"actions": {"on", "off", "toggle"}, "setting": None, "range": None, "unit": ""},
}


class DeviceError(Exception):
    """A request that can't be carried out; the message is shown to the user."""


@dataclass
class DeviceState:
    on: bool = False
    level: int | None = None   # Brightness (%) or fan speed.


@dataclass
class Device:
    id: str
    name: str
    kind: str
    aliases: list[str] = field(default_factory=list)
    confirm: bool = True
    allowed: set[str] | None = None   # Defaults to everything the kind supports.

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"Unknown device kind '{self.kind}' for {self.name}.")
        supported = KINDS[self.kind]["actions"]
        self.allowed = set(self.allowed) & supported if self.allowed else set(supported)

    @property
    def setting(self) -> str | None:
        return KINDS[self.kind]["setting"]

    @property
    def range(self) -> tuple[int, int] | None:
        return KINDS[self.kind]["range"]

    @property
    def unit(self) -> str:
        return KINDS[self.kind]["unit"]

    def names(self) -> list[str]:
        return [self.name.lower()] + [a.lower() for a in self.aliases]

    def describe(self, state: DeviceState) -> str:
        if not state.on:
            return f"The {self.name} is off"
        if self.setting and state.level is not None:
            return f"The {self.name} is on ({self.setting} {state.level}{self.unit})"
        return f"The {self.name} is on"


@dataclass(frozen=True)
class Command:
    """A validated change to a device."""

    action: str               # on | off | toggle | set
    value: int | None = None

    def describe(self, device: Device) -> str:
        if self.action == "set":
            return f"set the {device.name} {device.setting} to {self.value}{device.unit}"
        if self.action == "toggle":
            return f"toggle the {device.name}"
        return f"turn the {device.name} {self.action}"
