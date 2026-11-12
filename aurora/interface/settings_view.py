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
from aurora.interface.voice_control import input_devices
from aurora.interface.windows import integration

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
        self.performance = QComboBox()
        for text, value in (("Automatic: lighter while the PC is busy", "auto"), ("Always on", "on"),
                            ("Off", "off")):
            self.performance.addItem(text, value)
        self.performance.setCurrentIndex(max(0, self.performance.findData(values["performance_mode"])))
        self.performance.setToolTip("Lighter graphics (a still AI core while idle, 5 frames a second while busy, "
                                    "no page fades) keep more of the CPU for the model.\nAutomatic turns on "
                                    "after 6 seconds of CPU above 85% or RAM above 92%.")
        form.addRow("Performance mode", self.performance)
        interface.add_layout(form)
        self.body.addWidget(interface)

        voice = Panel("Voice (Chat's microphone button and spoken replies, and the terminal's listen / voice on)")
        form = QFormLayout()
        self.voice_name = QComboBox()
        self.voice_name.addItems(["Zira", "David", ""])
        self.voice_name.setCurrentText(values["voice_name"])
        form.addRow("Voice", self.voice_name)
        self.voice_rate = QSpinBox()
        self.voice_rate.setRange(80, 300)
        self.voice_rate.setValue(values["voice_rate"])
        form.addRow("Speaking speed (words/min)", self.voice_rate)
        self.voice_volume = QSpinBox()
        self.voice_volume.setRange(0, 100)
        self.voice_volume.setSuffix(" %")
        self.voice_volume.setValue(round(values["voice_volume"] * 100))
        form.addRow("Volume", self.voice_volume)
        self.voice_device = QComboBox()
        self.voice_device.addItem("Windows default microphone", "")
        for name in input_devices():
            self.voice_device.addItem(name, name)
        index = self.voice_device.findData(values["voice_input_device"])
        self.voice_device.setCurrentIndex(max(0, index))
        form.addRow("Microphone", self.voice_device)
        self.barge_in = QCheckBox("Stop speaking when I start talking (use headphones: with speakers, "
                                  "the microphone hears AURORA and cuts it off)")
        self.barge_in.setChecked(values["voice_barge_in"])
        form.addRow("", self.barge_in)
        voice.add_layout(form)
        tests = QHBoxLayout()
        test_voice = QPushButton("Test the voice")
        test_voice.clicked.connect(self._test_voice)
        test_mic = QPushButton("Test the microphone")
        test_mic.clicked.connect(self._test_microphone)
        tests.addWidget(test_voice)
        tests.addWidget(test_mic)
        tests.addStretch(1)
        voice.add_layout(tests)
        self.voice_result = voice.add(label(
            "The microphone is opened only when you press Chat's microphone button or Test the microphone. "
            "Speech is recognised on this laptop (Vosk) and never saved.", "Faint", wrap=True))
        self.body.addWidget(voice)

        network = Panel("Internet and network")
        form = QFormLayout()
        self.internet = QComboBox()
        for text, mode in (("Ask before each service first goes online", "ask"),
                           ("Allowed without asking", "on"),
                           ("Off: nothing goes to the internet", "off")):
            self.internet.addItem(text, mode)
        self.internet.setCurrentIndex(max(0, self.internet.findData(values["internet_access"])))
        form.addRow("Internet access", self.internet)
        self.local_network = QCheckBox("Allow AURORA to reach the sensor hub and device controller on my Wi-Fi")
        self.local_network.setChecked(values["local_network_access"])
        form.addRow("", self.local_network)
        network.add_layout(form)
        services = "; ".join(
            f"{name} → {', '.join(info['hosts'])} (sends {info['sends']})"
            for name, info in settings.NETWORK_SERVICES.items() if info.get("hosts"))
        network.add(label(
            "Your conversations, memories and documents never leave this PC. Only these services go online, "
            f"each only to its own hosts: {services}. Every request is listed on the System page.",
            "Faint", wrap=True))
        self.body.addWidget(network)

        windows = Panel("Windows")
        form = QFormLayout()
        self.minimize_to_tray = QCheckBox("Minimising the window sends AURORA to the notification area")
        self.minimize_to_tray.setChecked(values["minimize_to_tray"])
        form.addRow("", self.minimize_to_tray)
        self.close_to_tray = QCheckBox("Closing the window keeps AURORA running in the notification area")
        self.close_to_tray.setChecked(values["close_to_tray"])
        form.addRow("", self.close_to_tray)
        self.startup = QCheckBox("Start AURORA when I sign in to Windows (minimised to the notification area)")
        self.startup.setChecked(integration.startup_enabled())
        self.startup.setEnabled(integration.IS_WINDOWS)
        form.addRow("", self.startup)
        windows.add_layout(form)
        shortcut_row = QHBoxLayout()
        shortcut = QPushButton("Create desktop shortcut")
        shortcut.clicked.connect(self._create_shortcut)
        shortcut.setEnabled(integration.IS_WINDOWS)
        shortcut_row.addWidget(shortcut)
        shortcut_row.addStretch(1)
        windows.add_layout(shortcut_row)
        windows.add(label("To pin AURORA to the taskbar: right-click its taskbar button → Pin to taskbar "
                          "(Windows doesn't let apps pin themselves). Exit AURORA from the tray menu.",
                          "Faint", wrap=True))
        self.body.addWidget(windows)

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
            "performance_mode": self.performance.currentData(),
            "voice_name": self.voice_name.currentText(),
            "voice_rate": self.voice_rate.value(),
            "voice_volume": self.voice_volume.value() / 100,
            "voice_input_device": self.voice_device.currentData() or "",
            "voice_barge_in": self.barge_in.isChecked(),
            "minimize_to_tray": self.minimize_to_tray.isChecked(),
            "close_to_tray": self.close_to_tray.isChecked(),
            "internet_access": self.internet.currentData(),
            "local_network_access": self.local_network.isChecked(),
        }

    def _test_voice(self):
        """Speaks one sentence with the voice settings on this page (saved first)."""
        self.backend.save_preferences(self.values())
        self.voice_result.setText("Speaking…")
        self.backend.voice.speak("Hello, I'm AURORA. This is how I sound.",
                                 lambda finished: self.voice_result.setText(
                                     "Done." if finished else "Stopped."))

    def _test_microphone(self):
        """Listens once and shows what was heard (nothing is sent to AURORA)."""
        self.backend.save_preferences(self.values())
        self.voice_result.setText("🎙  Say something, for example \"what is the weather in Delhi\"…")
        self.backend.voice.listen(
            lambda text: self.voice_result.setText(f"I heard: “{text}”" if text else
                                                   "I didn't hear anything. Check the microphone and its volume."),
            lambda error: self.voice_result.setText(f"The microphone isn't available: {error}"))

    def _create_shortcut(self):
        try:
            path = integration.create_shortcut()
        except Exception as error:
            QMessageBox.warning(self, "Desktop shortcut", f"Couldn't create the shortcut: {error}")
            return
        QMessageBox.information(self, "Desktop shortcut", f"Created {path.name} on your desktop.")

    def _apply_startup(self) -> bool:
        wanted = self.startup.isChecked()
        if not integration.IS_WINDOWS or wanted == integration.startup_enabled():
            return True
        try:
            integration.set_startup(wanted)
            return True
        except Exception as error:
            QMessageBox.warning(self, "Start with Windows", f"Couldn't change the startup setting: {error}")
            self.startup.setChecked(integration.startup_enabled())
            return False

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
        if self._apply_startup():
            QMessageBox.information(self, "Settings", "Saved. New settings are in use.")

    def on_shown(self):
        self.model.setCurrentText(settings.DEFAULT_MODEL)
