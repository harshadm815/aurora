from aurora.providers.ollama_provider import OllamaProvider


class Brain:

    def __init__(self):

        self.provider = OllamaProvider()

    def ask(self, messages):

        return self.provider.chat(messages)