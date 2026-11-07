"""Phase 11: Model manager, Training monitor, AURORA runtime and training from the Command Centre."""

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from aurora.brain.brain import Brain
from aurora.config import preferences, settings
from aurora.interface import models_catalog
from aurora.interface.backend import Backend, wait_for
from aurora.interface.models_catalog import (
    find_core_models, find_training_runs, read_metrics, training_command, training_texts,
)
from aurora.kernel.engine import AuroraEngine
from aurora.providers.base_provider import BaseProvider
from aurora.tool_plugins.registry import default_tools
from aurora_core.tokenizer import ByteBPETokenizer
from aurora_core.training import save_checkpoint
from aurora_core.transformer import AuroraT0, T0Config
from test_weather import client_returning

TEXT = "".join(f"The cat sat on mat number {i % 7}. The dog ran to the park.\n\n" for i in range(400))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def models_dir():
    """A Models folder (temporary, see conftest) with real and broken checkpoints."""
    root = settings.MODELS_DIR
    tok = ByteBPETokenizer()
    model = AuroraT0(T0Config(vocab_size=tok.vocab_size, context_length=16, d_model=16, n_heads=2), seed=0)
    save_checkpoint(root / "run_a" / "best.npz", model, step=120, tokenizer=tok)
    save_checkpoint(root / "run_a" / "best_int8.npz", model, step=120, tokenizer=tok, precision="int8")
    save_checkpoint(root / "bare" / "no_tokenizer.npz", model, step=5)
    (root / "broken.npz").write_bytes(b"not an npz")
    np.savez(root / "other.npz", x=np.zeros(3))
    (root / "run_a" / "summary.json").write_text(json.dumps({"test_loss": 3.25}), encoding="utf-8")
    return root


class OverviewProvider(BaseProvider):
    """A conversation provider that reports Ollama-style model details."""

    model = "tinyllama"

    def __init__(self):
        self.unloaded = []

    def chat(self, messages):
        return "Hello."

    def health_check(self):
        return True

    def installed_models(self):
        return ["phi3:latest", "tinyllama:latest"]

    def model_overview(self):
        return {"active": self.model, "installed": [
            {"name": "phi3:latest", "size": 2_200_000_000, "parameters": "3.8B", "quantization": "Q4_0",
             "loaded": False, "memory": 0, "family": "phi3", "modified": None},
            {"name": "tinyllama:latest", "size": 640_000_000, "parameters": "1B", "quantization": "Q4_0",
             "loaded": True, "memory": 900_000_000, "family": "llama", "modified": None},
        ]}

    def unload(self, model=None):
        self.unloaded.append(model)


@pytest.fixture
def backend(qapp, memory, tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    provider = OverviewProvider()

    def factory():
        return AuroraEngine(Brain(provider), memory=memory, tools=default_tools(memory, client_returning()))

    b = Backend(engine_factory=factory)
    b.provider = provider
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    yield b
    b.shutdown()


# ============================================================ catalog


def test_find_core_models(models_dir):
    models = {m.name: m for m in find_core_models()}
    assert set(models) == {"run_a/best.npz", "run_a/best_int8.npz", "bare/no_tokenizer.npz", "broken.npz", "other.npz"}
    best = models["run_a/best.npz"]
    assert best.readable and best.step == 120 and best.has_tokenizer and best.precision == "float32"
    assert best.parameters == AuroraT0(T0Config(**best.config)).num_parameters
    assert best.summary == {"test_loss": 3.25}
    assert models["run_a/best_int8.npz"].precision == "int8"
    assert not models["bare/no_tokenizer.npz"].has_tokenizer
    assert not models["broken.npz"].readable and not models["other.npz"].readable
    assert "unreadable" in models["other.npz"].describe()


def test_find_core_models_without_a_folder():
    assert find_core_models() == []


def test_training_runs_and_half_written_metrics(tmp_path):
    root = settings.MODELS_DIR
    (root / "done").mkdir(parents=True)
    (root / "done" / "metrics.jsonl").write_text(
        '{"step": 10, "train_loss": 5.0, "validation_loss": 5.1}\n{"step": 20, "train_loss": 4.0, "validation_loss": 4.2}\n',
        encoding="utf-8")
    (root / "done" / "summary.json").write_text(json.dumps({"train_config": {"steps": 20}}), encoding="utf-8")
    (root / "live").mkdir()
    (root / "live" / "metrics.jsonl").write_text('{"step": 5, "train_loss": 6.0, "validation_loss": 6.1}\n{"step": 1',
                                                 encoding="utf-8")
    runs = {r.name: r for r in find_training_runs()}
    assert runs["done"].finished and runs["done"].planned_steps == 20 and runs["done"].best_validation == 4.2
    assert not runs["live"].finished and runs["live"].last_step == 5
    assert read_metrics(tmp_path / "missing.jsonl") == []


def test_training_texts():
    settings.DATA_DIR.mkdir(parents=True)
    (settings.DATA_DIR / "book.txt").write_text("x", encoding="utf-8")
    (settings.DATA_DIR / "notes.md").write_text("x", encoding="utf-8")
    assert [p.name for p in training_texts()] == ["book.txt"]


def test_training_command(tmp_path, monkeypatch):
    command = training_command(tmp_path / "d.txt", tmp_path / "out", 100, 0.01, 512)
    assert command[:2] == [sys.executable, str(models_catalog.Path(models_catalog.__file__).resolve().parents[2]
                                               / "command_centre.py")]
    assert command[2:5] == ["--train", "--data", str(tmp_path / "d.txt")]
    assert command[-2:] == ["--eval-interval", "10"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert training_command(tmp_path / "d.txt", tmp_path / "o", 5, 0.01, 300)[:2] == [sys.executable, "--train"]
    with pytest.raises(ValueError):
        training_command(tmp_path / "d.txt", tmp_path / "o", 0, 0.01, 512)
    with pytest.raises(ValueError):
        training_command(tmp_path / "d.txt", tmp_path / "o", 10, 0.01, 100)


def test_train_entry_point_runs_without_a_window(tmp_path):
    from aurora.interface.app import run

    data = tmp_path / "data.txt"
    data.write_text(TEXT, encoding="utf-8")
    out = tmp_path / "out"
    assert run(["command_centre.py", "--train", "--data", str(data), "--out", str(out), "--steps", "20",
                "--vocab-size", "280", "--eval-interval", "10", "--batch-size", "4"]) == 0
    assert {"best.npz", "metrics.jsonl", "summary.json"} <= {p.name for p in out.iterdir()}


# ============================================================ Ollama provider details


def test_ollama_model_overview():
    from aurora.providers.ollama_provider import OllamaProvider

    details = SimpleNamespace(parameter_size="1.1B", quantization_level="Q4_0", family="llama")
    client = SimpleNamespace(
        list=lambda: SimpleNamespace(models=[
            SimpleNamespace(model="tinyllama:latest", size=637_000_000, modified_at=datetime(2026, 1, 1), details=details),
            SimpleNamespace(model="phi3:latest", size=2_000_000_000, modified_at=None, details=None)]),
        ps=lambda: SimpleNamespace(models=[SimpleNamespace(model="tinyllama:latest", size=800_000_000)]),
        generate=lambda **kwargs: client.calls.append(kwargs),
        calls=[],
    )
    provider = OllamaProvider(model="tinyllama", client=client)
    overview = provider.model_overview()
    assert overview["active"] == "tinyllama"
    assert [(m["name"], m["loaded"], m["memory"], m["parameters"]) for m in overview["installed"]] == [
        ("phi3:latest", False, 0, ""), ("tinyllama:latest", True, 800_000_000, "1.1B")]
    provider.unload("phi3:latest")
    provider.unload()
    assert [c["model"] for c in client.calls] == ["phi3:latest", "tinyllama"]


# ============================================================ AURORA runtime through the backend


def test_load_generate_and_unload_an_aurora_model(backend, models_dir):
    core = backend.core
    loaded = []
    core.load(models_dir / "run_a" / "best.npz", on_done=loaded.append)
    assert wait_for(lambda: core.loaded, 10)
    assert loaded[0]["parameters"] > 0 and loaded[0]["step"] == 120 and len(loaded[0]["sha256"]) == 64

    pieces, stats = [], []
    core.generate("Hi", {"tokens": 30, "temperature": 0}, pieces.append, stats.append, pytest.fail)
    assert wait_for(lambda: stats, 10)
    assert stats[0]["new_tokens"] == 30 and stats[0]["stop_reason"] == "length"
    assert pieces                                       # streamed to the GUI thread

    core.unload()
    assert wait_for(lambda: not core.loaded, 10)


def test_generation_can_be_stopped(backend, models_dir):
    core = backend.core
    core.load(models_dir / "run_a" / "best.npz")
    assert wait_for(lambda: core.loaded, 10)
    stats = []
    core.generate("Hi", {"tokens": 2000, "temperature": 1.0}, lambda piece: core.stop_generation(), stats.append,
                  pytest.fail)
    assert wait_for(lambda: stats, 20)
    assert stats[0]["stop_reason"] == "cancelled" and stats[0]["new_tokens"] < 2000


def test_pausing_aurora_unloads_its_own_model(backend, models_dir):
    backend.core.load(models_dir / "run_a" / "best.npz")
    assert wait_for(lambda: backend.core.loaded, 10)
    backend.pause()
    assert wait_for(lambda: not backend.core.loaded, 10)


def test_a_model_without_a_tokenizer_fails_to_load(backend, models_dir):
    errors = []
    backend.core.load(models_dir / "bare" / "no_tokenizer.npz", on_error=errors.append)
    assert wait_for(lambda: errors, 10) and not backend.core.loaded


# ============================================================ training as a separate process


def test_training_run_from_the_command_centre(backend, tmp_path):
    data = tmp_path / "data.txt"
    data.write_text(TEXT, encoding="utf-8")
    states = []
    backend.core.training_changed.connect(states.append)
    out = settings.MODELS_DIR / "gui_run"
    backend.core.start_training(data, out, steps=20, lr=0.01, vocab_size=280)
    assert backend.core.training
    with pytest.raises(RuntimeError):
        backend.core.start_training(data, out, steps=20, lr=0.01, vocab_size=280)    # one at a time
    assert wait_for(lambda: len(states) == 2, 120)
    assert states[0]["running"] and states[1] == {"running": False, "out": str(out), "exit": 0, "error": ""}
    assert (out / "summary.json").is_file() and read_metrics(out / "metrics.jsonl")


def test_training_can_be_stopped(backend, tmp_path):
    data = tmp_path / "data.txt"
    data.write_text(TEXT, encoding="utf-8")
    states = []
    backend.core.training_changed.connect(states.append)
    backend.core.start_training(data, settings.MODELS_DIR / "long", steps=100_000, lr=0.01, vocab_size=280)
    assert wait_for(lambda: backend.core.training, 10)
    backend.core.stop_training()
    assert wait_for(lambda: len(states) == 2, 20)
    assert not backend.core.training and states[1]["exit"] != 0


def test_shutdown_stops_a_training_run(qapp, memory, tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    b = Backend(engine_factory=lambda: AuroraEngine(Brain(OverviewProvider()), memory=memory, tools=[]))
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    data = tmp_path / "data.txt"
    data.write_text(TEXT, encoding="utf-8")
    b.core.start_training(data, settings.MODELS_DIR / "long", steps=100_000, lr=0.01, vocab_size=280)
    assert wait_for(lambda: b.core.training, 10)
    b.shutdown()
    assert not b.core.training


# ============================================================ pages


@pytest.fixture
def window(backend):
    from aurora.interface.main_window import MainWindow

    w = MainWindow(backend)
    w.show()
    yield w
    w.close()


def test_main_window_has_the_new_pages(window):
    assert {"Models", "Training"} <= set(window.pages)


def test_model_manager_lists_and_runs_models(window, backend, models_dir):
    page = window.pages["Models"]
    window.open_page("Models")
    assert wait_for(lambda: page.core_table.rowCount() == 5, 10)
    assert wait_for(lambda: page.ollama_table.rowCount() == 2, 10)
    names = [page.ollama_table.item(r, 0).text() for r in range(2)]
    assert names == ["phi3:latest", "tinyllama:latest  (chat)"]
    assert page.ollama_table.item(1, 4).text() == "900.0 MB"

    row = [page.core_table.item(r, 0).text() for r in range(5)].index("run_a/best.npz")
    assert page.core_table.item(row, 5).text() == "3.250"
    page.core_table.selectRow(row)
    page._load_core()
    assert wait_for(lambda: backend.core.loaded and "Loaded: run_a/best.npz" in page.core_status.text(), 10)
    page.prompt.setText("Hi")
    page.tokens.setValue(20)
    page._generate()
    assert wait_for(lambda: "tokens/s" in page.stats.text(), 10)
    assert page.output.toPlainText().startswith("Hi") and len(page.output.toPlainText()) > 2


def test_model_manager_switches_and_unloads_ollama_models(window, backend, models_dir):
    page = window.pages["Models"]
    window.open_page("Models")
    assert wait_for(lambda: page.ollama_table.rowCount() == 2, 10)
    page.ollama_table.selectRow(0)
    page._use_for_chat()
    assert backend.preferences["model"] == "phi3"            # ":latest" dropped, as in Settings
    page._unload_ollama()
    assert wait_for(lambda: backend.provider.unloaded == ["phi3:latest"], 10)


def test_training_monitor_shows_runs_and_charts(window, backend):
    root = settings.MODELS_DIR / "past"
    root.mkdir(parents=True)
    (root / "metrics.jsonl").write_text("\n".join(json.dumps({"step": s, "train_loss": 6 - s / 100,
                                                              "validation_loss": 6.1 - s / 100}) for s in (100, 200, 300)),
                                        encoding="utf-8")
    (root / "summary.json").write_text(json.dumps({
        "train_config": {"steps": 300}, "test_loss": 3.2, "test_perplexity": 24.5,
        "test_baselines": {"bigram": 3.7}, "validation_baselines": {"bigram": 3.74, "unigram": 5.27},
        "parameters": 30176, "training_seconds": 60}), encoding="utf-8")
    page = window.pages["Training"]
    window.open_page("Training")
    assert wait_for(lambda: page.run_list.count() == 1, 10)
    assert page.chart.ranges() is not None
    assert [s[0] for s in page.chart.series] == ["training", "validation"]
    assert [b[2] for b in page.chart.baselines] == [3.74, 5.27]
    assert "beats the bigram baseline (3.700)" in page.details.text()
    page.chart.grab()                                       # paints without errors


def test_training_monitor_starts_and_follows_a_run(window, backend):
    settings.DATA_DIR.mkdir(parents=True)
    (settings.DATA_DIR / "tiny.txt").write_text(TEXT, encoding="utf-8")
    page = window.pages["Training"]
    window.open_page("Training")
    page.data.setCurrentText("tiny.txt")
    page.name.setText("from gui!")
    page.steps.setValue(20)
    page.vocab.setValue(280)
    page._start()
    assert not page.start_button.isEnabled() and page.stop_button.isEnabled()
    assert wait_for(lambda: page.progress.value() == 100, 120)
    assert "Finished" in page.progress_text.text()
    assert (settings.MODELS_DIR / "from_gui" / "best.npz").is_file()          # unsafe characters replaced
    assert wait_for(lambda: page.run_list.count() == 1 and "finished" in page.run_list.item(0).text(), 10)


def test_every_tool_has_a_description(backend):
    from aurora.interface.tools_view import DESCRIPTIONS

    missing = [name for name in backend.engine.tool_status() if name not in DESCRIPTIONS]
    assert missing == []
