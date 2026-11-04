from abc import ABC, abstractmethod

from aurora.kernel.lifecycle import ServiceState


class BaseService(ABC):

    def __init__(self, name: str):
        self.name = name
        self.state = ServiceState.STOPPED

    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    def status(self):
        return self.state