import ollama

from aurora.providers.base_provider import BaseProvider


class OllamaProvider(BaseProvider):

    def __init__(self, model="tinyllama"):
        self.model = model

    def chat(self, messages):

        response = ollama.chat(
            model=self.model,
            messages=messages
        )

        return response["message"]["content"]