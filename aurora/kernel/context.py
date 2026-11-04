from dataclasses import dataclass, field

from aurora.events.bus import EventBus
from aurora.kernel.permissions import PermissionManager


@dataclass
class KernelContext:
    """
    The shared kernel facilities a plugin is allowed to use.

    Plugins receive this instead of the whole engine, so they can only
    reach what the kernel deliberately exposes.
    """

    events: EventBus = field(default_factory=EventBus)
    permissions: PermissionManager = field(default_factory=PermissionManager)
