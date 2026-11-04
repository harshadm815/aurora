import logging

import ollama

from aurora.config import settings
from aurora.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):

    def __init__(self, model: str = settings.DEFAULT_MODEL):
        self.model = model

    def health_check(self) -> None:
        """Raises if Ollama is unreachable or the model isn't installed."""
        installed = {model.model for model in ollama.list().models}
        wanted = self.model if ":" in self.model else f"{self.model}:latest"

        if wanted not in installed:
            raise RuntimeError(
                f"Model '{self.model}' is not installed. Run: ollama pull {self.model}"
            )

    def chat(self, messages):

        logger.debug("Sending %d messages to %s", len(messages), self.model)

        response = ollama.chat(
            model=self.model,
            messages=messages
        )

        return response["message"]["content"]
