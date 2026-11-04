import pytest

from aurora.kernel.permissions import Permission, PermissionDeniedError, PermissionManager
from aurora.tools.tool import BaseTool
from aurora.tools.tool_manager import ToolManager


def test_everything_is_denied_by_default():
    permissions = PermissionManager()

    for permission in Permission:
        assert permissions.is_allowed("Anything", permission) is False


def test_grants_are_per_component():
    permissions = PermissionManager({"Weather": ["network"]})

    assert permissions.is_allowed("Weather", Permission.NETWORK)
    assert not permissions.is_allowed("Camera", Permission.NETWORK)
    assert not permissions.is_allowed("Weather", Permission.HARDWARE_CONTROL)


def test_grant_and_revoke():
    permissions = PermissionManager()

    permissions.grant("Camera", Permission.CAMERA)
    assert permissions.is_allowed("Camera", "camera")

    permissions.revoke("Camera", Permission.CAMERA)
    assert not permissions.is_allowed("Camera", "camera")


def test_require_raises_when_denied():
    permissions = PermissionManager()

    with pytest.raises(PermissionDeniedError) as info:
        permissions.require("Camera", Permission.FILESYSTEM_READ)

    assert info.value.component == "Camera"
    assert info.value.permission is Permission.FILESYSTEM_READ


def test_unknown_permission_in_config_is_rejected():
    with pytest.raises(ValueError, match="Unknown permission"):
        PermissionManager({"Weather": ["internet"]})


def test_missing_lists_only_ungranted():
    permissions = PermissionManager({"Hub": ["sensors"]})

    missing = permissions.missing("Hub", [Permission.SENSORS, Permission.HEALTH_DATA])

    assert missing == [Permission.HEALTH_DATA]


# --- Tools are gated by permissions ---------------------------------------

class FileReaderTool(BaseTool):
    required_permissions = (Permission.FILESYSTEM_READ,)

    def __init__(self):
        super().__init__("FileReader")

    def execute(self):
        return "file contents"


def test_tool_without_permission_is_refused():
    manager = ToolManager(PermissionManager())
    manager.register(FileReaderTool())

    with pytest.raises(PermissionDeniedError):
        manager.execute("FileReader")


def test_tool_with_permission_runs():
    manager = ToolManager(PermissionManager({"FileReader": ["filesystem.read"]}))
    manager.register(FileReaderTool())

    assert manager.execute("filereader") == "file contents"


def test_duplicate_tool_rejected():
    manager = ToolManager()
    manager.register(FileReaderTool())

    with pytest.raises(ValueError):
        manager.register(FileReaderTool())
