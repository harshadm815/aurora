import logging

import httpx
import ollama

from aurora.config import settings
from aurora.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class ModelUnavailableError(RuntimeError):
    """The language model couldn't produce an answer."""


class OllamaProvider(BaseProvider):

    def __init__(
        self,
        model: str = settings.DEFAULT_MODEL,
        client: ollama.Client | None = None,
    ):
        self.model = model
        # The timeout stops a stalled model from freezing AURORA.
        self.client = client or ollama.Client(timeout=settings.MODEL_TIMEOUT_SECONDS)

    def health_check(self) -> None:
        """Raises if Ollama is unreachable or the model isn't installed."""
        installed = {model.model for model in self.client.list().models}
        wanted = self.model if ":" in self.model else f"{self.model}:latest"

        if wanted not in installed:
            raise RuntimeError(
                f"Model '{self.model}' is not installed. Run: ollama pull {self.model}"
            )

    def chat(self, messages):

        logger.debug("Sending %d messages to %s", len(messages), self.model)

        try:
            response = self.client.chat(
                model=self.model,
                messages=messages,
                # Caps the reply length so a looping model can't run forever.
                options={"num_predict": settings.MODEL_MAX_REPLY_TOKENS},
            )
        except httpx.TimeoutException:
            logger.warning("Model %s timed out", self.model)
            raise ModelUnavailableError(
                f"The language model didn't answer within "
                f"{settings.MODEL_TIMEOUT_SECONDS} seconds. Please try again."
            ) from None
        except (httpx.ConnectError, ConnectionError):
            raise ModelUnavailableError(
                "I can't reach Ollama. Is it running? (Start it with: ollama serve)"
            ) from None

        return response["message"]["content"]
