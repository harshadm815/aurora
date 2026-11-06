from collections.abc import Iterator

from aurora.providers.base_provider import BaseProvider
from aurora.providers.ollama_provider import OllamaProvider


class Brain:
    """
    Reasoning and language. Delegates to a model provider, so the
    provider can be swapped (or faked in tests) without touching callers.
    """

    def __init__(self, provider: BaseProvider | None = None):

        self.provider = provider or OllamaProvider()

    def ask(self, messages):

        return self.provider.chat(messages)

    def ask_stream(self, messages) -> Iterator[str]:
        """The reply in pieces, as the model writes it."""

        return self.provider.chat_stream(messages)
