from aurora.kernel.service import BaseService


class ServiceManager:

    def __init__(self):
        self.services = {}

    def register(self, service: BaseService):
        self.services[service.name] = service

    def start_all(self):
        for service in self.services.values():
            service.start()

    def stop_all(self):
        for service in self.services.values():
            service.stop()

    def status(self):
        return {
            name: service.status().value
            for name, service in self.services.items()
        }