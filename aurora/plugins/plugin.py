from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from aurora.kernel.permissions import Permission

if TYPE_CHECKING:
    from aurora.kernel.context import KernelContext


class BasePlugin(ABC):
    """
    Base class for every AURORA plugin.

    Subclasses list the permissions they need in ``required_permissions``.
    The PluginManager only initializes a plugin if every one of them has
    been granted. At runtime a plugin can reach the kernel's event bus
    through ``self.context.events`` and re-check a permission with
    ``self.require(...)``.
    """

    required_permissions: ClassVar[tuple[Permission, ...]] = ()

    def __init__(self, name: str):
        self.name = name
        self.enabled = True
        self.context: "KernelContext | None" = None

    def attach(self, context: "KernelContext") -> None:
        """Called by the PluginManager before ``initialize()``."""
        self.context = context

    def require(self, permission: Permission) -> None:
        """Raises PermissionDeniedError if this plugin lacks ``permission``."""
        if self.context is None:
            raise RuntimeError(f"Plugin '{self.name}' is not attached to a kernel.")
        self.context.permissions.require(self.name, permission)

    def publish(self, event_name: str, data=None) -> None:
        """Publishes an event with this plugin as the source."""
        if self.context is None:
            raise RuntimeError(f"Plugin '{self.name}' is not attached to a kernel.")
        self.context.events.emit(event_name, data, source=self.name)

    @abstractmethod
    def initialize(self):
        """Initialize the plugin."""

    @abstractmethod
    def shutdown(self):
        """Shutdown the plugin."""
