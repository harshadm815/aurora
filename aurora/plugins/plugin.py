from abc import ABC, abstractmethod


class BasePlugin(ABC):
    """
    Base class for every AURORA plugin.
    """

    def __init__(self, name: str):
        self.name = name
        self.enabled = True

    @abstractmethod
    def initialize(self):
        """Initialize the plugin."""
        pass

    @abstractmethod
    def shutdown(self):
        """Shutdown the plugin."""
        pass