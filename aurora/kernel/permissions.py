"""
Permission system for AURORA components.

Every capability that touches something sensitive (files, network,
camera, health data, hardware) is a separate permission. Nothing is
allowed unless it has been explicitly granted to that component by
name. Grants come from ``settings.PERMISSION_GRANTS``, which the user
controls; components cannot grant permissions to themselves.
"""

import logging
from collections.abc import Iterable, Mapping
from enum import Enum

logger = logging.getLogger(__name__)


class Permission(Enum):
    MEMORY_READ = "memory.read"
    MEMORY_WRITE = "memory.write"
    FILESYSTEM_READ = "filesystem.read"
    FILESYSTEM_WRITE = "filesystem.write"
    NETWORK = "network"
    CAMERA = "camera"
    MICROPHONE = "microphone"
    SENSORS = "sensors"
    HEALTH_DATA = "health_data"
    HARDWARE_CONTROL = "hardware_control"


class PermissionDeniedError(PermissionError):

    def __init__(self, component: str, permission: Permission):
        self.component = component
        self.permission = permission
        super().__init__(
            f"'{component}' does not have permission '{permission.value}'."
        )


class PermissionManager:
    """Deny-by-default record of which component may do what."""

    def __init__(self, grants: Mapping[str, Iterable[str | Permission]] | None = None):
        self._grants: dict[str, set[Permission]] = {}

        for component, permissions in (grants or {}).items():
            for permission in permissions:
                self.grant(component, permission)

    @staticmethod
    def _to_permission(permission: str | Permission) -> Permission:
        if isinstance(permission, Permission):
            return permission
        try:
            return Permission(permission)
        except ValueError:
            valid = ", ".join(p.value for p in Permission)
            raise ValueError(
                f"Unknown permission '{permission}'. Valid permissions: {valid}"
            ) from None

    def grant(self, component: str, permission: str | Permission) -> None:
        permission = self._to_permission(permission)
        self._grants.setdefault(component, set()).add(permission)
        logger.info("Granted %s to %s", permission.value, component)

    def revoke(self, component: str, permission: str | Permission) -> None:
        permission = self._to_permission(permission)
        self._grants.get(component, set()).discard(permission)
        logger.info("Revoked %s from %s", permission.value, component)

    def is_allowed(self, component: str, permission: str | Permission) -> bool:
        return self._to_permission(permission) in self._grants.get(component, set())

    def missing(
        self,
        component: str,
        permissions: Iterable[str | Permission],
    ) -> list[Permission]:
        """Returns the permissions in ``permissions`` not granted to ``component``."""
        return [
            p for p in map(self._to_permission, permissions)
            if not self.is_allowed(component, p)
        ]

    def require(self, component: str, permission: str | Permission) -> None:
        """Raises PermissionDeniedError unless the permission is granted."""
        permission = self._to_permission(permission)

        if not self.is_allowed(component, permission):
            logger.warning("Denied %s to %s", permission.value, component)
            raise PermissionDeniedError(component, permission)

    def granted(self, component: str) -> set[Permission]:
        return set(self._grants.get(component, set()))
