"""
The Command Centre's connection to AURORA's own models (aurora_core):

- the AURORA Core Runtime: load a checkpoint, generate text (streamed,
  stoppable), unload it again;
- training runs: started as a separate process (so the window stays
  responsive and a crash can't take AURORA down), followed through their
  metrics.jsonl, and stoppable.

Owned by the Backend, which unloads the model when AURORA is paused and
stops everything when it shuts down.
"""

import logging
import threading
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from aurora.config import settings
from aurora.interface.models_catalog import training_command

logger = logging.getLogger(__name__)


class CoreModels(QObject):

    model_changed = Signal(object)         # dict (loaded model info) or None
    training_changed = Signal(object)      # dict: {"running": bool, "out": str, "exit": int|None, "error": str}

    def __init__(self, backend):
        super().__init__(backend)
        self.backend = backend
        self._runtime = None
        self.info: dict | None = None
        self._cancel: threading.Event | None = None
        self.process: QProcess | None = None
        self.training_out: Path | None = None
        self._stderr: deque[str] = deque(maxlen=20)

    # --------------------------------------------------------------- runtime

    @property
    def runtime(self):
        if self._runtime is None:
            from aurora_core.runtime import AuroraRuntime   # NumPy is only loaded when needed
            self._runtime = AuroraRuntime()
        return self._runtime

    @property
    def loaded(self) -> bool:
        return self.info is not None

    def load(self, path: Path, on_done=None, on_error=None) -> None:
        def work():
            info = self.runtime.load(path)
            memory = self.runtime.memory_usage()
            return {"path": str(path), "name": Path(path).parent.name + "/" + Path(path).name,
                    "sha256": info.sha256, "parameters": info.parameters, "config": info.config,
                    "tokenizer": info.tokenizer, "step": info.training_step, "memory": memory}

        def done(info):
            self.info = info
            self.model_changed.emit(info)
            if on_done:
                on_done(info)

        self.backend.run_io(work, done, on_error)

    def unload(self, on_done=None) -> None:
        if self._runtime is None or not self.loaded:
            return

        def work():
            self.stop_generation()
            self._runtime.unload()

        def done(_):
            self.info = None
            self.model_changed.emit(None)
            if on_done:
                on_done(None)

        self.backend.run_io(work, done, lambda error: logger.warning("Unloading failed: %s", type(error).__name__))

    def generate(self, prompt: str, options: dict, on_text, on_done, on_error) -> None:
        """Streams text to on_text (on the GUI thread); on_done gets the stats."""
        from aurora_core.generation import GenerationConfig

        config = GenerationConfig(max_new_tokens=int(options.get("tokens", 150)),
                                  temperature=float(options.get("temperature", 0.5)),
                                  top_k=options.get("top_k", 40) or None, seed=options.get("seed"))
        self._cancel = threading.Event()
        cancel = self._cancel

        def work():
            result = self.runtime.generate(prompt, config, on_text=lambda piece: self.backend.on_gui(on_text, piece),
                                           cancel=cancel)
            return result.stats.to_dict()

        self.backend.run_io(work, on_done, on_error)

    def stop_generation(self) -> None:
        if self._cancel is not None:
            self._cancel.set()

    # --------------------------------------------------------------- training

    @property
    def training(self) -> bool:
        return self.process is not None and self.process.state() != QProcess.NotRunning

    def start_training(self, data: Path, out: Path, steps: int, lr: float, vocab_size: int) -> None:
        if self.training:
            raise RuntimeError("A training run is already in progress.")
        command = training_command(data, out, steps, lr, vocab_size)
        out.mkdir(parents=True, exist_ok=True)
        self.training_out = out
        self._stderr.clear()
        process = QProcess(self)
        process.setWorkingDirectory(str(settings.PROJECT_ROOT))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.readyReadStandardError.connect(self._read_errors)
        process.readyReadStandardOutput.connect(lambda: process.readAllStandardOutput())   # drain; metrics come from the file
        process.finished.connect(self._finished)
        self.process = process
        logger.info("Training started (%d steps)", steps)
        process.start(command[0], command[1:])
        self.training_changed.emit({"running": True, "out": str(out), "exit": None, "error": ""})

    def _read_errors(self) -> None:
        text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        self._stderr.extend(line for line in text.splitlines() if line.strip())

    def _finished(self, exit_code: int, _status) -> None:
        error = "" if exit_code == 0 else (self._stderr[-1] if self._stderr else "it stopped unexpectedly")
        logger.info("Training finished (exit code %d)", exit_code)
        self.training_changed.emit({"running": False, "out": str(self.training_out), "exit": exit_code,
                                    "error": error})

    def stop_training(self, wait_ms: int = 3000) -> None:
        if not self.training:
            return
        logger.info("Training stopped by the user")
        self.process.terminate()
        if not self.process.waitForFinished(wait_ms):
            self.process.kill()
            self.process.waitForFinished(wait_ms)

    # --------------------------------------------------------------- lifecycle

    def shutdown(self) -> None:
        self.stop_generation()
        self.stop_training()
        if self._runtime is not None and self.loaded:
            try:
                self._runtime.unload()
            except Exception:
                logger.warning("Unloading AURORA's model failed")
            self.info = None
