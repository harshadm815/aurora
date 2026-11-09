import logging

import httpx
import ollama

from aurora.config import settings
from aurora.providers.base_provider import BaseProvider, ModelUnavailableError

logger = logging.getLogger(__name__)

__all__ = ["ModelUnavailableError", "OllamaProvider"]


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

    def chat_stream(self, messages):
        """Yields the reply piece by piece as the model writes it."""

        logger.debug("Streaming %d messages to %s", len(messages), self.model)

        try:
            for part in self.client.chat(
                model=self.model,
                messages=messages,
                stream=True,
                options={"num_predict": settings.MODEL_MAX_REPLY_TOKENS},
            ):
                text = part["message"]["content"]
                if text:
                    yield text
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

    def unload(self, model: str | None = None) -> None:
        """Asks Ollama to free a model's memory now (it reloads when next used).
        Without a name, the model AURORA chats with."""
        self.client.generate(model=model or self.model, keep_alive=0)
        logger.info("Model unloaded")

    def model_overview(self) -> dict:
        """Installed models with their size and details, and which are in memory
        (for the Model manager)."""
        loaded = {m.model: m for m in self.client.ps().models}
        installed = []
        for model in self.client.list().models:
            details = model.details
            in_memory = loaded.get(model.model)
            installed.append({
                "name": model.model,
                "size": model.size or 0,
                "modified": model.modified_at,
                "parameters": getattr(details, "parameter_size", None) or "",
                "quantization": getattr(details, "quantization_level", None) or "",
                "family": getattr(details, "family", None) or "",
                "loaded": in_memory is not None,
                "memory": (in_memory.size or 0) if in_memory is not None else 0,
            })
        return {"installed": sorted(installed, key=lambda m: m["name"]), "active": self.model}

    def loaded_models(self) -> list[str]:
        """Models currently held in memory by Ollama."""
        return [model.model for model in self.client.ps().models]

    def installed_models(self) -> list[str]:
        """Names of the models installed in Ollama (for the Settings page)."""
        return sorted(model.model for model in self.client.list().models)
