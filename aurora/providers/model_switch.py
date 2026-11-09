"""
The Brain's model interface: one provider that serves whichever model is
chosen in Settings.

- "aurora:<folder>" → AURORA's own model through the Core Runtime
  (core_provider.py);
- any other name → that model in Ollama.

Setting `model` switches at once; a Core model you switch away from is
unloaded, so the two never hold memory together. Callers (the Brain,
Settings, the Model manager, pause/resume) keep using the same methods
they used with Ollama alone.
"""

import logging
from pathlib import Path

from aurora.config import settings
from aurora.providers.base_provider import BaseProvider
from aurora.providers.core_provider import CoreRuntimeProvider, core_models, is_core_model
from aurora.providers.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)


class ModelSwitch(BaseProvider):

    def __init__(self, model: str | None = None, ollama: OllamaProvider | None = None,
                 models_dir: Path | None = None, core_factory=None):
        model = model or settings.DEFAULT_MODEL
        self.ollama = ollama or OllamaProvider(model="tinyllama" if is_core_model(model) else model)
        self.models_dir = models_dir
        self._core_factory = core_factory or (lambda name: CoreRuntimeProvider(name, self.models_dir))
        self.core: CoreRuntimeProvider | None = None
        self.model = model

    # ------------------------------------------------------------ choice

    @property
    def model(self) -> str:
        return self.core.model if self.core is not None else self.ollama.model

    @model.setter
    def model(self, name: str) -> None:
        if self.core is not None and self.core.model != name:
            try:
                self.core.unload()
            except Exception:
                logger.warning("Couldn't unload %s", self.core.model)
            self.core = None
        if is_core_model(name):
            if self.core is None:
                self.core = self._core_factory(name)
        else:
            self.ollama.model = name
        logger.info("Language model: %s", name)

    @property
    def active(self) -> BaseProvider:
        return self.core if self.core is not None else self.ollama

    @property
    def runs_locally_without_ollama(self) -> bool:
        return self.core is not None

    # --------------------------------------------------- the Brain's calls

    def chat(self, messages):
        return self.active.chat(messages)

    def chat_stream(self, messages):
        return self.active.chat_stream(messages)

    def health_check(self) -> None:
        self.active.health_check()

    def unload(self, model: str | None = None) -> None:
        """Frees a model's memory. Without a name, the model in use."""
        if model is None or model == self.model:
            self.active.unload()
        elif not is_core_model(model):
            self.ollama.unload(model)

    # ------------------------------------------- Settings and Model manager

    def installed_models(self) -> list[str]:
        """Ollama's models (when it's reachable) and AURORA's own."""
        try:
            ollama = self.ollama.installed_models()
        except Exception:
            if self.core is None:
                raise
            ollama = []
        return sorted(ollama) + core_models(self.models_dir)

    def model_overview(self) -> dict:
        """Ollama's models for the Model manager; "active" is the model in use."""
        return {**self.ollama.model_overview(), "active": self.model}

    def loaded_models(self) -> list[str]:
        return self.ollama.loaded_models()
