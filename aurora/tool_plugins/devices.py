"""
Devices tool: "turn on my desk light", "set the fan speed to 2",
"is the fan on?", "list my devices".

Every change asks "yes / no" first, unless you have set
``"confirm": False`` for that device yourself. Checks (known device,
allowed action, value range, rate limit) happen in DeviceController.
"""

from aurora.devices.controller import DeviceController, build_controller
from aurora.devices.device import DeviceError
from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult
from aurora.tools.tool import BaseTool

CHANGES = {"on", "off", "toggle", "set"}
SIMULATED = " (simulated device)"


class DevicesTool(BaseTool):

    required_permissions = (Permission.HARDWARE_CONTROL,)

    def __init__(self, controller: DeviceController | None = None, client=None):
        super().__init__("Devices")
        self._controller = controller
        self._client = client

    @property
    def controller(self) -> DeviceController:
        if self._controller is None:
            self._controller = build_controller(client=self._client)
        return self._controller

    def _tag(self, device) -> str:
        return SIMULATED if self.controller.driver(device).simulated else ""

    def execute(self, action: str = "list", device: str = "", value=None, **_) -> ToolResult:
        try:
            if action == "list":
                return self.list()

            guessed = ""
            try:
                target = self.controller.find(device)
            except DeviceError:
                # A near miss ("desk life", as speech recognition may hear "desk light"):
                # suggest the close match, and always ask before changing anything.
                target = self.controller.suggest(device)
                if target is None:
                    raise
                guessed = f"I don't have a device called \"{device}\". Did you mean the {target.name}? "

            if action == "status":
                state = self.controller.state(target)
                return ToolResult(True, f"{guessed}{target.describe(state)}.{self._tag(target)}", state)

            if action not in CHANGES:
                return ToolResult(False, "I can turn devices on or off, or set them to a level.")

            command = self.controller.validate(target, action, value)
        except DeviceError as error:
            return ToolResult(False, str(error))

        def carry_out() -> ToolResult:
            try:
                state = self.controller.apply(target, command)
            except DeviceError as error:
                return ToolResult(False, str(error))
            return ToolResult(True, f"Done. {target.describe(state)}.{self._tag(target)}", state)

        if not target.confirm and not guessed:
            return carry_out()

        return ToolResult(
            True,
            f"{guessed}Shall I {command.describe(target)}?{self._tag(target)} (yes / no)",
            command,
            confirm=carry_out,
        )

    def list(self) -> ToolResult:
        devices = list(self.controller.devices.values())
        if not devices:
            return ToolResult(True, "No devices are set up (DEVICES in settings).", [])

        lines = []
        for device in devices:
            try:
                status = self.controller.state(device)
                line = device.describe(status).replace("The ", "", 1)
            except DeviceError as error:
                line = f"{device.name}: unknown ({error})"
            lines.append(f"  - {line}{self._tag(device)}")
        return ToolResult(True, "Your devices:\n" + "\n".join(lines), [d.name for d in devices])
