import logging

from aurora.kernel.lifecycle import ServiceState
from aurora.kernel.service import BaseService

logger = logging.getLogger(__name__)


class ServiceManager:
    """
    Registers services and controls their lifecycle.

    A service that fails to start or stop is marked ERROR and logged;
    it does not stop the other services.
    """

    def __init__(self):
        self.services: dict[str, BaseService] = {}

    def register(self, service: BaseService):
        if service.name in self.services:
            raise ValueError(f"Service '{service.name}' is already registered.")

        self.services[service.name] = service

    def get(self, name: str) -> BaseService:
        try:
            return self.services[name]
        except KeyError:
            raise KeyError(f"Service '{name}' is not registered.") from None

    def start_all(self):
        for service in self.services.values():
            service.state = ServiceState.STARTING
            try:
                service.start()
                logger.info("Service started: %s", service.name)
            except Exception:
                service.state = ServiceState.ERROR
                logger.exception("Service failed to start: %s", service.name)

    def stop_all(self):
        # Stop in reverse order of registration.
        for service in reversed(list(self.services.values())):
            if service.state is ServiceState.STOPPED:
                continue
            service.state = ServiceState.STOPPING
            try:
                service.stop()
                logger.info("Service stopped: %s", service.name)
            except Exception:
                service.state = ServiceState.ERROR
                logger.exception("Service failed to stop: %s", service.name)

    def status(self):
        return {
            name: service.status().value
            for name, service in self.services.items()
        }
