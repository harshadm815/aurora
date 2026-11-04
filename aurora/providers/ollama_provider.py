import logging

import ollama

from aurora.config import settings
from aurora.providers.base_provider import BaseProvider

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):

    def __init__(self, model: str = settings.DEFAULT_MODEL):
        self.model = model

    def chat(self, messages):

        logger.debug("Sending %d messages to %s", len(messages), self.model)

        response = ollama.chat(
            model=self.model,
            messages=messages
        )

        return response["message"]["content"]
