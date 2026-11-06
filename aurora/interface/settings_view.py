"""
Settings. Saved to aurora_preferences.json (ignored by git) and applied
straight away. Permissions, devices and paths are deliberately NOT
editable here: they stay in settings.py / local_settings.py.
"""

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
)

from aurora.config import preferences, settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Panel, label

PAGES = ["Dashboard", "Chat", "Memory", "Knowledge", "Tools", "Vision", "Sensors", "System", "Settings"]


class SettingsView(BaseView):
    title = "Settings"
    subtitle = "Changes apply immediately and are kept for next time."
    nav_icon = "⚙"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        values = backend.preferences

        ai = Panel("AI")
        form = QFormLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.addItem(values["model"])
        form.addRow("Model", self.model)
        self.personality = QPlainTextEdit(values["personality"])
        self.personality.setFixedHeight(110)
        form.addRow("Personality", self.personality)
        reset = QPushButton("Restore default personality")
        reset.clicked.connect(lambda: self.personality.setPlainText(preferences.defaults()["personality"]))
        form.addRow("", reset)
        self.streaming = QCheckBox("Show replies as they are written (streaming)")
        self.streaming.setChecked(values["chat_streaming"])
        form.addRow("", self.streaming)
        ai.add_layout(form)
        self.body.addWidget(ai)

        memory = Panel("Memory")
        form = QFormLayout()
        self.announce = QCheckBox("Tell me when AURORA saves or updates a memory")
        self.announce.setChecked(values["memory_announce"])
        form.addRow("", self.announce)
        self.context_limit = QSpinBox()
        self.context_limit.setRange(0, 20)
        self.context_limit.setValue(values["memory_context_limit"])
        form.addRow("Memories given to the model", self.context_limit)
        memory.add_layout(form)
        memory.add(label("Automatic learning is switched on or off with the AutoMemory grant in "
                         "PERMISSION_GRANTS (settings.py), not here.", "Faint", wrap=True))
        self.body.addWidget(memory)

        interface = Panel("Interface")
        form = QFormLayout()
        self.accent = QComboBox()
        self.accent.addItems(["cyan", "blue", "purple"])
        self.accent.setCurrentText(values["accent"])
        form.addRow("Accent colour", self.accent)
        self.start_page = QComboBox()
        self.start_page.addItems(PAGES)
        self.start_page.setCurrentText(values["start_page"])
        form.addRow("Open on start", self.start_page)
        self.maximized = QCheckBox("Start maximised")
        self.maximized.setChecked(values["start_maximized"])
        form.addRow("", self.maximized)
        self.animations = QCheckBox("Subtle animations")
        self.animations.setChecked(values["animations"])
        form.addRow("", self.animations)
        interface.add_layout(form)
        self.body.addWidget(interface)

        voice = Panel("Voice (used by the terminal's listen / voice on commands)")
        form = QFormLayout()
        self.voice_name = QComboBox()
        self.voice_name.addItems(["Zira", "David", ""])
        self.voice_name.setCurrentText(values["voice_name"])
        form.addRow("Voice", self.voice_name)
        self.voice_rate = QSpinBox()
        self.voice_rate.setRange(80, 300)
        self.voice_rate.setValue(values["voice_rate"])
        form.addRow("Speaking speed (words/min)", self.voice_rate)
        voice.add_layout(form)
        voice.add(label("Voice conversations inside the Command Centre are planned; for now use "
                        "`listen` or `voice on` in the terminal.", "Faint", wrap=True))
        self.body.addWidget(voice)

        buttons = QHBoxLayout()
        save = QPushButton("Save settings")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        buttons.addStretch(1)
        buttons.addWidget(label(f"Saved to {preferences.PATH.name}", "Faint"))
        self.body.addLayout(buttons)
        self.body.addStretch(1)

        backend.ollama_checked.connect(self._models)

    def _models(self, state: dict):
        current = self.model.currentText()
        models = state.get("models") or []
        self.model.blockSignals(True)
        self.model.clear()
        self.model.addItems(sorted(set(models) | {current}))
        self.model.setCurrentText(current)
        self.model.blockSignals(False)

    def values(self) -> dict:
        model = self.model.currentText().strip()
        # Ollama lists "tinyllama:latest"; settings use the short name.
        if model.endswith(":latest"):
            model = model[: -len(":latest")]
        return {
            "model": model,
            "personality": self.personality.toPlainText().strip(),
            "chat_streaming": self.streaming.isChecked(),
            "memory_announce": self.announce.isChecked(),
            "memory_context_limit": self.context_limit.value(),
            "accent": self.accent.currentText(),
            "start_page": self.start_page.currentText(),
            "start_maximized": self.maximized.isChecked(),
            "animations": self.animations.isChecked(),
            "voice_name": self.voice_name.currentText(),
            "voice_rate": self.voice_rate.value(),
        }

    def save(self):
        values = self.values()
        if not values["model"] or not values["personality"]:
            QMessageBox.warning(self, "Settings", "The model and personality can't be empty.")
            return
        installed = self.backend.ollama.get("models") or []
        if installed and f"{values['model']}:latest" not in installed and values["model"] not in installed:
            answer = QMessageBox.question(
                self, "Model not installed",
                f"Ollama doesn't list '{values['model']}'. Save anyway? (Install it with: ollama pull {values['model']})",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.backend.save_preferences(values)
        QMessageBox.information(self, "Settings", "Saved. New settings are in use.")

    def on_shown(self):
        self.model.setCurrentText(settings.DEFAULT_MODEL)
