"""
Training monitor: AURORA's training runs, past and present.

- Every run under Models/ (a folder with metrics.jsonl): loss curves for
  training and validation against the baselines it must beat, and its
  summary (test loss, perplexity, time).
- A new run on a text file from Data/, started as a separate process
  (the window stays responsive, and a crash can't take AURORA down),
  followed live through its metrics file, with progress, time left and
  a Stop button.
"""

import math
import re
import time
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLineEdit, QListWidget, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Panel, label
from aurora.interface.models_catalog import TrainingRun, find_training_runs, read_metrics, training_texts
from aurora.interface.styles.theme import palette

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class LossChart(QWidget):
    """Line chart of loss against training step, with dashed baseline lines."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.series: list[tuple[str, str, list[tuple[float, float]]]] = []
        self.baselines: list[tuple[str, str, float]] = []
        self.message = "Select a training run."

    def set_data(self, series, baselines, message: str = ""):
        self.series = [(name, colour, points) for name, colour, points in series if points]
        self.baselines = [b for b in baselines if b[2] is not None and math.isfinite(b[2])]
        self.message = message
        self.update()

    def ranges(self):
        xs = [x for _, _, pts in self.series for x, _ in pts]
        ys = [y for _, _, pts in self.series for _, y in pts] + [v for _, _, v in self.baselines]
        if not xs:
            return None
        low, high = min(ys), max(ys)
        pad = (high - low) * 0.08 or 0.5
        return 0.0, max(xs), max(0.0, low - pad), high + pad

    def paintEvent(self, event):
        p = palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(p.surface))
        bounds = self.ranges()
        if bounds is None:
            painter.setPen(QColor(p.text_faint))
            painter.drawText(self.rect(), Qt.AlignCenter, self.message or "No data yet.")
            return
        x0, x1, y0, y1 = bounds
        area = QRectF(56, 16, self.width() - 72, self.height() - 60)

        def point(x, y):
            px = area.left() + (x - x0) / ((x1 - x0) or 1) * area.width()
            py = area.bottom() - (y - y0) / ((y1 - y0) or 1) * area.height()
            return QPointF(px, py)

        small = QFont(self.font())
        small.setPointSizeF(max(7.5, small.pointSizeF() - 1))
        painter.setFont(small)
        grid = QPen(QColor(p.border))
        for i in range(5):                                     # horizontal grid and loss labels
            value = y0 + (y1 - y0) * i / 4
            y = point(x0, value).y()
            painter.setPen(grid)
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.setPen(QColor(p.text_faint))
            painter.drawText(QRectF(0, y - 8, 50, 16), Qt.AlignRight | Qt.AlignVCenter, f"{value:.2f}")
        for i in range(5):                                     # step labels
            value = x0 + (x1 - x0) * i / 4
            x = point(value, y0).x()
            painter.drawText(QRectF(x - 40, area.bottom() + 4, 80, 16), Qt.AlignCenter, f"{value:,.0f}")
        painter.drawText(QRectF(area.left(), area.bottom() + 20, area.width(), 16), Qt.AlignCenter, "training step")

        for name, colour, value in self.baselines:
            pen = QPen(QColor(colour), 1, Qt.DashLine)
            painter.setPen(pen)
            y = point(x0, value).y()
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            painter.drawText(QRectF(area.left() + 4, y - 16, area.width() - 8, 14), Qt.AlignRight,
                             f"{name} {value:.2f}")

        legend_x = area.left() + 8
        for name, colour, points in self.series:
            pen = QPen(QColor(colour), 2)
            painter.setPen(pen)
            path = QPainterPath(point(*points[0]))
            for x, y in points[1:]:
                path.lineTo(point(x, y))
            painter.drawPath(path)
            painter.setBrush(QColor(colour))
            for x, y in points:
                painter.drawEllipse(point(x, y), 2.5, 2.5)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(legend_x, area.top() + 8), QPointF(legend_x + 18, area.top() + 8))
            painter.setPen(QColor(p.text_dim))
            painter.drawText(QPointF(legend_x + 24, area.top() + 12), name)
            legend_x += 34 + painter.fontMetrics().horizontalAdvance(name)


def chart_data(run: TrainingRun, accent: str):
    p = palette(accent)
    series = [
        ("training", p.text_dim, [(m["step"], m["train_loss"]) for m in run.metrics if "train_loss" in m]),
        ("validation", p.accent, [(m["step"], m["validation_loss"]) for m in run.metrics if "validation_loss" in m]),
    ]
    baselines = []
    for name, colour in (("bigram", p.warning), ("unigram", p.text_faint)):
        value = (run.summary.get("validation_baselines") or {}).get(name)
        baselines.append((f"{name} baseline", colour, value))
    return series, baselines


def describe(run: TrainingRun) -> str:
    s = run.summary
    if not run.metrics:
        return "No measurements yet."
    last = run.metrics[-1]
    parts = [f"Step {last['step']:,}" + (f" of {run.planned_steps:,}" if run.planned_steps else ""),
             f"training loss {last['train_loss']:.3f}", f"validation {last['validation_loss']:.3f}",
             f"best validation {run.best_validation:.3f}"]
    if s:
        parts.append(f"test loss {s['test_loss']:.3f} (perplexity {s['test_perplexity']:.1f})")
        bigram = (s.get("test_baselines") or {}).get("bigram")
        if bigram is not None:
            parts.append(("beats" if s["test_loss"] < bigram else "doesn't beat") + f" the bigram baseline ({bigram:.3f})")
        parts.append(f"{s.get('parameters', 0):,} parameters, {s.get('training_seconds', 0):.0f} s")
    return " · ".join(parts)


class TrainingMonitorView(BaseView):
    title = "Training monitor"
    subtitle = "AURORA's own training runs: loss curves against their baselines, and new runs started from here."
    nav_icon = "📈"
    scrollable = False

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        self.runs: list[TrainingRun] = []
        self.started_at: float | None = None
        self.planned_steps = 0

        split = QSplitter()
        self.run_list = QListWidget()
        self.run_list.currentRowChanged.connect(self._show_run)
        split.addWidget(self.run_list)
        right = QWidget()
        column = QVBoxLayout(right)
        column.setContentsMargins(0, 0, 0, 0)
        self.chart = LossChart()
        column.addWidget(self.chart, 1)
        self.details = label("", "Faint", wrap=True)
        column.addWidget(self.details)
        split.addWidget(right)
        split.setSizes([240, 760])
        self.body.addWidget(split, 1)

        panel = Panel("Train a new model (AURORA-T0)")
        row = QHBoxLayout()
        self.data = QComboBox()
        self.name = QLineEdit("aurora_t0_new")
        self.name.setPlaceholderText("output folder name")
        self.steps = QSpinBox()
        self.steps.setRange(10, 100_000)
        self.steps.setValue(2000)
        self.steps.setSuffix(" steps")
        self.lr = QDoubleSpinBox()
        self.lr.setDecimals(4)
        self.lr.setRange(0.0001, 0.1)
        self.lr.setSingleStep(0.001)
        self.lr.setValue(0.01)
        self.lr.setPrefix("learning rate ")
        self.vocab = QSpinBox()
        self.vocab.setRange(260, 8000)
        self.vocab.setValue(512)
        self.vocab.setPrefix("vocabulary ")
        self.start_button = QPushButton("Start training")
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("Danger")
        self.stop_button.clicked.connect(self._stop)
        for widget in (self.data, self.name, self.steps, self.lr, self.vocab, self.start_button, self.stop_button):
            row.addWidget(widget)
        panel.add_layout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        panel.add(self.progress)
        self.progress_text = panel.add(label(
            f"Training text comes from {settings.DATA_DIR} (.txt files you have the right to use). "
            "Training uses the processor fully; the rest of AURORA keeps working, more slowly.", "Faint", wrap=True))
        self.body.addWidget(panel)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        backend.core.training_changed.connect(self._training_changed)
        self._set_running(backend.core.training)

    # ------------------------------------------------------------ runs

    def on_shown(self):
        self._load_runs()
        files = training_texts()
        current = self.data.currentText()
        self.data.clear()
        for path in files:
            self.data.addItem(path.name, str(path))
        if current:
            self.data.setCurrentText(current)
        if not files:
            self.data.addItem("(no .txt files in Data/)", None)

    def _load_runs(self, select: str | None = None):
        def show(runs):
            current = select or (self.runs[self.run_list.currentRow()].name
                                 if 0 <= self.run_list.currentRow() < len(self.runs) else None)
            self.runs = runs
            self.run_list.blockSignals(True)
            self.run_list.clear()
            for run in runs:
                state = "finished" if run.finished else ("training…" if self._is_live(run) else "stopped")
                self.run_list.addItem(f"{run.name}\n{state} · {run.last_step:,} steps")
            self.run_list.blockSignals(False)
            names = [r.name for r in runs]
            if runs:
                self.run_list.setCurrentRow(names.index(current) if current in names else 0)
                self._show_run(self.run_list.currentRow())
            else:
                self.chart.set_data([], [], "No training runs yet. Start one below.")
                self.details.setText("")

        self.backend.run_io(find_training_runs, show)

    def _is_live(self, run: TrainingRun) -> bool:
        out = self.backend.core.training_out
        return self.backend.core.training and out is not None and Path(out).resolve() == run.folder.resolve()

    def _show_run(self, row: int):
        if not 0 <= row < len(self.runs):
            return
        run = self.runs[row]
        series, baselines = chart_data(run, self.backend.preferences.get("accent", "cyan"))
        self.chart.set_data(series, baselines, "Waiting for the first measurement…")
        self.details.setText(describe(run))

    # ----------------------------------------------------------- training

    def _start(self):
        data = self.data.currentData()
        if not data:
            QMessageBox.information(self, "Training", f"Put a .txt file in {settings.DATA_DIR} first.")
            return
        name = _SAFE_NAME.sub("_", self.name.text().strip()).strip("._")
        if not name:
            QMessageBox.information(self, "Training", "Give the run a folder name.")
            return
        out = settings.MODELS_DIR / name
        if (out / "metrics.jsonl").exists():
            answer = QMessageBox.question(self, "Training", f"{name} already has a training run. Replace it?",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        try:
            self.backend.core.start_training(Path(data), out, self.steps.value(), self.lr.value(), self.vocab.value())
        except Exception as error:
            QMessageBox.warning(self, "Training", str(error))
            return
        self.planned_steps = self.steps.value()
        self.started_at = time.monotonic()

    def _stop(self):
        self.backend.core.stop_training()

    def _set_running(self, running: bool):
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        if running:
            self.timer.start(1500)
        else:
            self.timer.stop()

    def _training_changed(self, state: dict):
        self._set_running(state["running"])
        if state["running"]:
            self.progress.setValue(0)
            self.progress_text.setText("Preparing: reading the text and training the tokenizer…")
        elif state["exit"] == 0:
            self.progress.setValue(100)
            self.progress_text.setText("Finished. The new model is in the Model manager.")
        else:
            self.progress_text.setText(f"Training stopped: {state['error']}" if state["error"]
                                       else "Training stopped.")
        self._load_runs(select=Path(state["out"]).name if state.get("out") else None)

    def _poll(self):
        out = self.backend.core.training_out
        if out is None:
            return
        metrics = read_metrics(Path(out) / "metrics.jsonl")
        if not metrics:
            return
        last = metrics[-1]
        done = last["step"] / max(self.planned_steps, 1)
        self.progress.setValue(int(done * 100))
        elapsed = time.monotonic() - (self.started_at or time.monotonic())
        remaining = elapsed / done - elapsed if done > 0 else 0
        self.progress_text.setText(
            f"Step {last['step']:,} of {self.planned_steps:,} · training loss {last['train_loss']:.3f} · "
            f"validation {last['validation_loss']:.3f} · about {remaining / 60:.0f} min left")
        name = Path(out).name
        if self.runs and any(r.name == name for r in self.runs):
            run = next(r for r in self.runs if r.name == name)
            run.metrics = metrics
            if self.run_list.currentRow() >= 0 and self.runs[self.run_list.currentRow()].name == name:
                self._show_run(self.run_list.currentRow())
        else:
            self._load_runs(select=name)
