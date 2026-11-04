from abc import ABC, abstractmethod
from typing import ClassVar

from aurora.kernel.permissions import Permission
from aurora.tools.result import ToolResult


class BaseTool(ABC):
    """
    Base class for every AURORA tool.

    Tools list the permissions they need in ``required_permissions``;
    the ToolManager refuses to run a tool unless all are granted.

    ``execute`` receives the router's arguments as keyword arguments and
    returns a ToolResult.
    """

    required_permissions: ClassVar[tuple[Permission, ...]] = ()

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def execute(self, **arguments) -> ToolResult:
        """
        Executes the tool.
        """
