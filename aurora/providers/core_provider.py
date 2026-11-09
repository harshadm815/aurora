"""
AURORA's own language models as the Brain's provider, through the AURORA
Core Runtime (aurora_core.runtime): no Ollama, no network, nothing leaves
the PC.

A checkpoint is chosen by name, "aurora:<folder>", meaning
Models/<folder>/best.npz. It is loaded the first time it's needed (so
NumPy and the weights take no memory until then) and unloaded on pause or
when you switch to another model.

The present models (AURORA-T0) are tiny and were trained on one book with
a context of 32 tokens. They continue text; they don't follow
instructions or remember the conversation. So the prompt is only your
latest message: the personality prompt, memories and earlier messages
would not fit and the model can't use them.
"""

import logging
import queue
import threading
from pathlib import Path

from aurora.config import settings
from aurora.providers.base_provider import BaseProvider, ModelUnavailableError

logger = logging.getLogger(__name__)

PREFIX = "aurora:"
CHECKPOINT = "best.npz"
REPLACEMENT = "�"


def is_core_model(name: str) -> bool:
    return isinstance(name, str) and name.startswith(PREFIX)


def checkpoint_path(name: str, models_dir: Path | None = None) -> Path:
    """Models/<folder>/best.npz for "aurora:<folder>". Only a plain folder
    name directly inside the models folder is accepted."""
    folder = name[len(PREFIX):] if is_core_model(name) else ""
    if not folder or folder in (".", "..") or any(c in folder for c in "/\\:"):
        raise ModelUnavailableError(f"'{name}' isn't a valid AURORA model name (expected aurora:<folder>).")
    return Path(models_dir or settings.MODELS_DIR) / folder / CHECKPOINT


def core_models(models_dir: Path | None = None) -> list[str]:
    """The AURORA models on this PC: every Models/<folder> with a best.npz."""
    root = Path(models_dir or settings.MODELS_DIR)
    try:
        folders = [d for d in root.iterdir() if d.is_dir() and (d / CHECKPOINT).is_file()]
    except OSError:
        return []
    return sorted(PREFIX + d.name for d in folders)


def prompt_from(messages) -> str:
    """The text the model continues: the latest message you wrote."""
    for message in reversed(list(messages)):
        if message.get("role") == "user" and str(message.get("content", "")).strip():
            return str(message["content"]).strip()
    raise ModelUnavailableError("There's no message for the model to continue.")


class CoreRuntimeProvider(BaseProvider):

    def __init__(self, model: str, models_dir: Path | None = None, runtime=None):
        self.model = model
        self.path = checkpoint_path(model, models_dir)
        self._runtime = runtime            # injectable in tests
        self._lock = threading.Lock()

    # ------------------------------------------------------------ loading

    @property
    def runtime(self):
        if self._runtime is None:
            from aurora_core.runtime import AuroraRuntime   # NumPy only when needed
            self._runtime = AuroraRuntime()
        return self._runtime

    @property
    def loaded(self) -> bool:
        return self._runtime is not None and self._runtime.loaded

    def _ensure_loaded(self):
        with self._lock:
            runtime = self.runtime
            if not runtime.loaded:
                if not self.path.is_file():
                    raise ModelUnavailableError(
                        f"AURORA's model '{self.model}' isn't on this PC ({self.path} is missing)."
                    )
                try:
                    info = runtime.load(self.path)
                except Exception as error:
                    raise ModelUnavailableError(f"AURORA's model '{self.model}' couldn't be loaded: {error}") from None
                logger.info("Loaded %s (%d parameters)", self.model, info.parameters)
            return runtime

    def health_check(self) -> None:
        """Raises unless the checkpoint exists and loads."""
        self._ensure_loaded()

    def unload(self, model: str | None = None) -> None:
        with self._lock:
            if self._runtime is not None and self._runtime.loaded:
                self._runtime.unload()
                logger.info("Unloaded %s", self.model)

    # --------------------------------------------------------- generation

    def _config(self):
        from aurora_core.generation import GenerationConfig
        return GenerationConfig(
            max_new_tokens=min(settings.CORE_MAX_REPLY_TOKENS, settings.MODEL_MAX_REPLY_TOKENS),
            temperature=settings.CORE_TEMPERATURE,
            top_k=40,
        )

    def chat(self, messages):
        return "".join(self.chat_stream(messages))

    def chat_stream(self, messages):
        """Yields the continuation piece by piece. Closing the generator
        early (or an error) stops the runtime."""
        prompt = prompt_from(messages)
        runtime = self._ensure_loaded()
        config = self._config()
        pieces: queue.Queue = queue.Queue()
        cancel = threading.Event()
        done = object()

        def work():
            try:
                runtime.generate(prompt, config, on_text=pieces.put, cancel=cancel)
                pieces.put(done)
            except Exception as error:      # handed to the caller below
                pieces.put(error)

        logger.debug("Generating with %s", self.model)
        worker = threading.Thread(target=work, name="aurora-core-generate", daemon=True)
        worker.start()
        try:
            while True:
                try:
                    item = pieces.get(timeout=settings.MODEL_TIMEOUT_SECONDS)
                except queue.Empty:
                    raise ModelUnavailableError(
                        f"AURORA's model didn't answer within {settings.MODEL_TIMEOUT_SECONDS} seconds."
                    ) from None
                if item is done:
                    return
                if isinstance(item, Exception):
                    raise ModelUnavailableError(f"AURORA's model couldn't answer: {item}") from None
                # A tiny byte-level model sometimes writes bytes that aren't
                # valid UTF-8; their replacement character isn't shown.
                item = item.replace(REPLACEMENT, "")
                if item:
                    yield item
        finally:
            cancel.set()
            worker.join(timeout=5)
