from abc import ABC, abstractmethod


class ModelUnavailableError(RuntimeError):
    """The language model couldn't produce an answer."""


class BaseProvider(ABC):
    """
    Abstract base class for AI providers.
    """

    @abstractmethod
    def chat(self, messages):
        pass

    def chat_stream(self, messages):
        """
        Yields the reply in pieces. Providers that can't stream keep this
        default, which yields the whole reply at once.
        """
        yield self.chat(messages)

    def health_check(self) -> None:
        """
        Raises an exception if the provider cannot serve requests.
        Providers that can't be checked cheaply keep this default.
        """
