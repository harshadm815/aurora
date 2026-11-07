"""
Model manager: the models AURORA uses.

- Conversation model (Ollama): installed models with their size and
  details, which are in memory, choose the one AURORA chats with, unload.
- AURORA's own models (aurora_core): every checkpoint under Models/, load
  one into the AURORA Core Runtime and try it (streamed, stoppable), unload.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
)

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Panel, label
from aurora.interface.models_catalog import find_core_models


def size_text(size: int) -> str:
    if size >= 1e9:
        return f"{size / 1e9:.1f} GB"
    if size >= 1e6:
        return f"{size / 1e6:.1f} MB"
    return f"{size / 1024:.0f} KB"


def table(columns: list[str], stretch: int = 0) -> QTableWidget:
    widget = QTableWidget(0, len(columns))
    widget.setHorizontalHeaderLabels(columns)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.verticalHeader().setVisible(False)
    widget.setAlternatingRowColors(True)
    header = widget.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setSectionResizeMode(stretch, QHeaderView.Stretch)
    return widget


def fill(widget: QTableWidget, rows: list[list[str]]) -> None:
    widget.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(value)
            if c > 0:
                item.setTextAlignment(Qt.AlignCenter)
            widget.setItem(r, c, item)


class ModelManagerView(BaseView):
    title = "Model manager"
    subtitle = ("The conversation model (through Ollama) and AURORA's own models, run by AURORA's "
                "own runtime. Load, try and unload them here.")
    nav_icon = "🧩"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.core_models = []
        self.ollama_models = []

        # --- Ollama ---
        chat = Panel("Conversation model (Ollama)")
        self.ollama_status = chat.add(label("", "Faint", wrap=True))
        self.ollama_table = table(["Model", "Parameters", "Quantization", "Size", "In memory"])
        self.ollama_table.setMinimumHeight(110)
        self.ollama_table.setMaximumHeight(200)
        chat.add(self.ollama_table)
        row = QHBoxLayout()
        for text, handler in [("Use for chat", self._use_for_chat), ("Unload from memory", self._unload_ollama),
                              ("Refresh", self._load_ollama)]:
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch(1)
        chat.add_layout(row)
        self.body.addWidget(chat)

        # --- AURORA's own models ---
        core = Panel("AURORA's own models (aurora_core)")
        core.add(label(f"Checkpoints in {settings.MODELS_DIR}. They run on AURORA's own runtime, without Ollama. "
                       "AURORA-T0 continues text in the style of the book it was trained on; it can't hold a "
                       "conversation, so it isn't used for Chat.", "Faint", wrap=True))
        self.core_table = table(["Checkpoint", "Parameters", "Precision", "Size", "Trained steps", "Test loss"])
        self.core_table.setMinimumHeight(200)
        core.add(self.core_table)
        row = QHBoxLayout()
        self.load_button = QPushButton("Load into runtime")
        self.load_button.clicked.connect(self._load_core)
        self.unload_button = QPushButton("Unload")
        self.unload_button.clicked.connect(lambda: self.backend.core.unload())
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._scan_core)
        for button in (self.load_button, self.unload_button, refresh):
            row.addWidget(button)
        row.addStretch(1)
        core.add_layout(row)
        self.core_status = core.add(label("No model loaded.", "Faint", wrap=True))
        self.body.addWidget(core)

        # --- Try it ---
        trial = Panel("Try the loaded model")
        row = QHBoxLayout()
        self.prompt = QLineEdit("Sherlock Holmes")
        self.prompt.returnPressed.connect(self._generate)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(0.5)
        self.temperature.setPrefix("temperature ")
        self.tokens = QSpinBox()
        self.tokens.setRange(1, 2000)
        self.tokens.setValue(150)
        self.tokens.setSuffix(" tokens")
        self.generate_button = QPushButton("Generate")
        self.generate_button.clicked.connect(self._generate)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(lambda: self.backend.core.stop_generation())
        self.stop_button.setEnabled(False)
        row.addWidget(self.prompt, 1)
        for widget in (self.temperature, self.tokens, self.generate_button, self.stop_button):
            row.addWidget(widget)
        trial.add_layout(row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(140)
        trial.add(self.output)
        self.stats = trial.add(label("", "Faint"))
        self.body.addWidget(trial)
        self.body.addStretch(1)

        backend.core.model_changed.connect(self._model_changed)
        backend.ollama_checked.connect(lambda _: self._load_ollama())
        self._model_changed(backend.core.info)

    # ------------------------------------------------------------ lifecycle

    def on_engine_ready(self):
        self.on_shown()

    def on_shown(self):
        self._scan_core()
        self._load_ollama()

    # --------------------------------------------------------------- Ollama

    def _load_ollama(self):
        engine = self.backend.engine
        if engine is None:
            self.ollama_status.setText("AURORA is starting…")
            return
        provider = engine.brain.provider
        if not hasattr(provider, "model_overview"):
            self.ollama_status.setText("The conversation model doesn't come from Ollama.")
            fill(self.ollama_table, [])
            return

        def show(overview):
            self.ollama_models = overview["installed"]
            active = overview["active"]
            self.ollama_status.setText(f"AURORA chats with {active}. Ollama is running.")
            fill(self.ollama_table, [
                [m["name"] + ("  (chat)" if m["name"].split(":")[0] == active.split(":")[0] else ""),
                 m["parameters"] or "—", m["quantization"] or "—", size_text(m["size"]),
                 size_text(m["memory"]) if m["loaded"] else "no"]
                for m in self.ollama_models])

        def failed(error):
            self.ollama_models = []
            fill(self.ollama_table, [])
            self.ollama_status.setText(f"Can't reach Ollama ({error}). Start it with: ollama serve")

        self.backend.run_io(provider.model_overview, show, failed)

    def _selected_ollama(self) -> dict | None:
        rows = self.ollama_table.selectionModel().selectedRows()
        return self.ollama_models[rows[0].row()] if rows else None

    def _use_for_chat(self):
        model = self._selected_ollama()
        if model is None:
            QMessageBox.information(self, "Model manager", "Select a model first.")
            return
        name = model["name"]
        self.backend.save_preferences({"model": name[: -len(":latest")] if name.endswith(":latest") else name})

    def _unload_ollama(self):
        model = self._selected_ollama()
        engine = self.backend.engine
        if model is None or engine is None:
            QMessageBox.information(self, "Model manager", "Select a model first.")
            return
        provider = engine.brain.provider
        self.backend.run_io(lambda: provider.unload(model["name"]), lambda _: self._load_ollama(),
                            lambda error: QMessageBox.warning(self, "Model manager", f"Couldn't unload: {error}"))

    # ----------------------------------------------------------- aurora_core

    def _scan_core(self):
        def show(models):
            self.core_models = models
            fill(self.core_table, [
                [m.name, f"{m.parameters:,}" if m.readable else "—", m.precision if m.readable else "—",
                 size_text(m.size), str(m.step) if m.readable else "—",
                 f"{m.summary['test_loss']:.3f}" if isinstance(m.summary.get("test_loss"), (int, float)) else "—"]
                for m in models])
            if not models:
                self.core_status.setText(f"No checkpoints in {settings.MODELS_DIR} yet. Train one in the "
                                         "Training monitor.")

        self.backend.run_io(find_core_models, show)

    def _load_core(self):
        rows = self.core_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Model manager", "Select a checkpoint first.")
            return
        model = self.core_models[rows[0].row()]
        if not model.readable or not model.has_tokenizer:
            QMessageBox.warning(self, "Model manager", f"{model.name} can't be run: "
                                f"{model.problem or 'it has no tokenizer'}.")
            return
        self.core_status.setText(f"Loading {model.name}…")
        self.backend.core.load(model.path, on_error=lambda error: self.core_status.setText(f"Couldn't load: {error}"))

    def _model_changed(self, info):
        loaded = info is not None
        self.unload_button.setEnabled(loaded)
        self.generate_button.setEnabled(loaded)
        if not loaded:
            self.core_status.setText("No model loaded.")
            return
        memory = info["memory"]
        self.core_status.setText(
            f"Loaded: {info['name']} · {info['parameters']:,} parameters · trained {info['step']} steps · "
            f"memory {size_text(memory['parameters'])} weights + {size_text(memory['kv_cache'])} cache · "
            f"sha256 {info['sha256'][:12]}…")

    def _generate(self):
        if not self.backend.core.loaded:
            return
        prompt = self.prompt.text()
        if not prompt.strip():
            return
        self.output.setPlainText(prompt)
        self.stats.setText("Generating…")
        self.generate_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        def piece(text):
            self.output.moveCursor(self.output.textCursor().MoveOperation.End)
            self.output.insertPlainText(text)

        def done(stats):
            self.generate_button.setEnabled(self.backend.core.loaded)
            self.stop_button.setEnabled(False)
            self.stats.setText(f"{stats['new_tokens']} tokens · {stats['tokens_per_second']:.0f} tokens/s · "
                               f"stopped: {stats['stop_reason']}")

        def failed(error):
            self.generate_button.setEnabled(self.backend.core.loaded)
            self.stop_button.setEnabled(False)
            self.stats.setText(f"That didn't work: {error}")

        self.backend.core.generate(prompt, {"temperature": self.temperature.value(), "tokens": self.tokens.value()},
                                   piece, done, failed)
