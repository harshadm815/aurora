from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """
    Abstract base class for AI providers.
    """

    @abstractmethod
    def chat(self, messages):
        pass