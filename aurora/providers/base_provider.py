from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """
    Abstract base class for AI providers.
    """

    @abstractmethod
    def chat(self, messages):
        pass

    def health_check(self) -> None:
        """
        Raises an exception if the provider cannot serve requests.
        Providers that can't be checked cheaply keep this default.
        """
