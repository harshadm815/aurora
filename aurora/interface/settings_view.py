"""
Settings. Saved to aurora_preferences.json (ignored by git) and applied
straight away: every change saves itself a moment after you make it
(no button to forget), and the line at the top says so. Permissions,
devices and paths are deliberately NOT editable here: they stay in
settings.py / local_settings.py.

A model name Ollama doesn't have is not saved (AURORA couldn't answer
with it); the rest still is.
"""

import logging
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QVBoxLayout,
)

from aurora.config import preferences, settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Panel, clear_layout, label
from aurora.interface.voice_control import input_devices
from aurora.interface.windows import integration

logger = logging.getLogger(__name__)

AUTOSAVE_MS = 600            # after a tick box, list or number changes
TYPING_MS = 1500             # after you stop typing the personality
WAKE_KEYS = ("wake_enabled", "wake_key", "wake_phrase", "wake_sound")

PAGES = ["Dashboard", "Chat", "Memory", "Knowledge", "Tools", "Vision", "Sensors", "System", "Settings"]


class SettingsView(BaseView):
    title = "Settings"
    subtitle = "Changes apply immediately and are kept for next time."
    nav_icon = "⚙"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        values = backend.preferences
        self.save_status = label("Changes are saved automatically.", "Faint")
        self.body.addWidget(self.save_status)

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
        self.voice_engine = QComboBox()
        self.voice_engine.addItem("Natural voice (Piper, offline)", "piper")
        self.voice_engine.addItem("Windows voices (offline)", "sapi")
        self.voice_engine.setCurrentIndex(max(0, self.voice_engine.findData(values["voice_synthesiser"])))
        self.voice_engine.setToolTip("Piper sounds more natural and uses about 250 MB of memory while speaking.\n"
                                     "If its voice file is missing, AURORA uses the Windows voice instead.")
        form.addRow("Voice engine", self.voice_engine)
        self.voice_name = QComboBox()
        self.voice_name.addItems(["Zira", "David", ""])
        self.voice_name.setCurrentText(values["voice_name"])
        self.voice_name.setToolTip("Which Windows voice (with the Windows voices engine).")
        form.addRow("Windows voice", self.voice_name)
        self.voice_rate = QSpinBox()
        self.voice_rate.setRange(80, 300)
        self.voice_rate.setValue(values["voice_rate"])
        form.addRow("Speaking speed (words/min)", self.voice_rate)
        self.voice_volume = QSpinBox()
        self.voice_volume.setRange(0, 100)
        self.voice_volume.setSuffix(" %")
        self.voice_volume.setValue(round(values["voice_volume"] * 100))
        form.addRow("Volume", self.voice_volume)
        self.voice_pitch = QSpinBox()
        self.voice_pitch.setRange(-10, 10)
        self.voice_pitch.setValue(values["voice_pitch"])
        self.voice_pitch.setToolTip("Lower (-10) or higher (10) than the voice's own pitch (0). Windows voices only.")
        form.addRow("Pitch", self.voice_pitch)
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
        self.speak_reminders = QCheckBox("Say reminders aloud when they're due")
        self.speak_reminders.setChecked(values["speak_reminders"])
        form.addRow("", self.speak_reminders)
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

        wake = Panel("Wake Engine (hold a key, say the phrase, and AURORA starts and listens)")
        form = QFormLayout()
        self.wake_enabled = QCheckBox("Run the Wake Engine in the background")
        self.wake_enabled.setChecked(values["wake_enabled"])
        self.wake_enabled.setToolTip("A small helper (about 45 MB) watches for the key. The microphone opens only "
                                     "while you hold it.")
        form.addRow("", self.wake_enabled)
        self.wake_key = QComboBox()
        self.wake_key.addItem("Hold Right Ctrl (doesn't affect typing)", "right_ctrl")
        self.wake_key.addItem("Hold Space (a space is typed on release; held Space no longer repeats)", "space")
        self.wake_key.setCurrentIndex(max(0, self.wake_key.findData(values["wake_key"])))
        form.addRow("Activation key", self.wake_key)
        self.wake_phrase = QLineEdit(values["wake_phrase"])
        self.wake_phrase.setMaxLength(40)
        self.wake_phrase.setToolTip("Up to four ordinary English words, letters only.")
        form.addRow("Wake phrase", self.wake_phrase)
        self.wake_sound = QCheckBox("Beep when the microphone opens")
        self.wake_sound.setChecked(values["wake_sound"])
        form.addRow("", self.wake_sound)
        self.wake_startup = QCheckBox("Start the Wake Engine when I sign in to Windows")
        from aurora.wake import launcher as wake_launcher
        self.wake_startup.setChecked(wake_launcher.helper_startup_enabled())
        self.wake_startup.setEnabled(integration.IS_WINDOWS)
        form.addRow("", self.wake_startup)
        wake.add_layout(form)
        wake.add(label("Hold the key for half a second: a small panel shows the microphone is on. Say the phrase "
                       "while holding it. Releasing the key closes the microphone; pressing another key cancels. "
                       "Nothing is recorded or saved.", "Faint", wrap=True))
        self.body.addWidget(wake)

        phone = Panel("Phone (the AURORA app on your Android phone: chat, today's briefing, reminders, PC status)")
        form = QFormLayout()
        self.mobile_enabled = QCheckBox("Let my phone use AURORA")
        self.mobile_enabled.setChecked(values["mobile_enabled"])
        self.mobile_enabled.setToolTip("Listens on this PC only (127.0.0.1), reached over the USB cable, plus your "
                                       "home Wi-Fi if you tick it below. Only your paired phone can use it.")
        form.addRow("", self.mobile_enabled)
        self.mobile_wifi = QCheckBox("Also connect over Wi-Fi, on my home networks only")
        self.mobile_wifi.setChecked(values["mobile_wifi"])
        self.mobile_wifi.setToolTip("Encrypted (HTTPS with a certificate your phone checks), and only while this "
                                    "PC is connected to a network listed below. Elsewhere AURORA doesn't listen "
                                    "on Wi-Fi at all.")
        form.addRow("", self.mobile_wifi)
        phone.add_layout(form)
        self._home_networks = list(values["mobile_home_networks"])
        home_row = QHBoxLayout()
        self.home_networks_label = label("", "Faint", wrap=True)
        home_row.addWidget(self.home_networks_label, 1)
        self.add_home = QPushButton("Mark this network as home")
        self.add_home.clicked.connect(self._add_home_network)
        self.clear_home = QPushButton("Forget home networks")
        self.clear_home.clicked.connect(self._clear_home_networks)
        home_row.addWidget(self.add_home)
        home_row.addWidget(self.clear_home)
        phone.add_layout(home_row)
        self.mobile_status = phone.add(label("", "Faint", wrap=True))
        pair_row = QHBoxLayout()
        self.pair_button = QPushButton("Pair my phone…")
        self.pair_button.clicked.connect(self._pair_phone)
        pair_row.addWidget(self.pair_button)
        pair_row.addStretch(1)
        phone.add_layout(pair_row)
        self.device_rows = QVBoxLayout()
        self.device_rows.setSpacing(4)
        phone.add_layout(self.device_rows)
        self.phone_activity = phone.add(label("", "Faint", wrap=True))
        phone.add(label(
            "Only one phone can be paired, and only over the USB cable: plug it in (USB debugging on), open the "
            "AURORA app and type the code from \"Pair my phone…\". The phone keeps a key in its secure hardware "
            "and signs everything it sends, so nothing else can pretend to be it. Away from home, Tailscale "
            "(tailscale serve --bg 8765) can carry the connection.", "Faint", wrap=True))
        self.body.addWidget(phone)
        backend.mobile_changed.connect(lambda _: self._refresh_phone())
        self._phone_timer = QTimer(self)
        self._phone_timer.setInterval(5000)
        self._phone_timer.timeout.connect(self._refresh_phone_status)

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
        save = QPushButton("Save now")
        save.setToolTip("Changes are saved automatically; this saves straight away.")
        save.clicked.connect(self.save)
        buttons.addWidget(save)
        buttons.addStretch(1)
        buttons.addWidget(label(f"Saved to {preferences.PATH.name}", "Faint"))
        self.body.addLayout(buttons)
        self.body.addStretch(1)

        backend.ollama_checked.connect(self._models)
        self._autosave = QTimer(self, singleShot=True, timeout=self.auto_save)
        self._watch_changes()

    # --- Saving -------------------------------------------------------------------

    def _watch_changes(self):
        """Every control saves itself shortly after it changes."""
        from PySide6.QtWidgets import QCheckBox as Check, QComboBox as Combo, QSpinBox as Spin

        def later(widget, delay=AUTOSAVE_MS):
            def start(*_):
                self._changed = widget                  # where to show "Saved" (the top line may be scrolled away)
                self._autosave.start(delay)
            return start

        self._changed = None
        for check in self.findChildren(Check):
            check.toggled.connect(later(check))
        for spin in self.findChildren(Spin):
            spin.valueChanged.connect(later(spin))
        for combo in self.findChildren(Combo):
            if combo is not self.model:
                combo.currentIndexChanged.connect(later(combo))
        self.model.activated.connect(later(self.model))                        # chosen from the list
        self.model.lineEdit().editingFinished.connect(later(self.model))       # typed, then Enter or click away
        self.wake_phrase.editingFinished.connect(later(self.wake_phrase))
        self.personality.textChanged.connect(later(self.personality, TYPING_MS))

    def _status(self, text: str, problem: bool = False):
        text = ("⚠  " if problem else "✓  ") + text
        widget, self._changed = self._changed, None
        if widget is not None and widget.isVisible():
            from PySide6.QtCore import QPoint
            from PySide6.QtWidgets import QToolTip
            # Next to the control just changed, for a moment.
            QToolTip.showText(widget.mapToGlobal(QPoint(0, widget.height())),
                              text if problem else "✓  Saved", widget, widget.rect(), 4000 if problem else 1500)
        self.save_status.setText(text)
        self.save_status.setObjectName("Warning" if problem else "Saved")
        self.save_status.style().unpolish(self.save_status)
        self.save_status.style().polish(self.save_status)

    def _installed(self, model: str) -> bool:
        installed = self.backend.ollama.get("models") or []
        from aurora.providers.core_provider import is_core_model
        return not installed or is_core_model(model) or model in installed or f"{model}:latest" in installed

    def auto_save(self) -> bool:
        """Saves what's on the page now. Returns True if everything was saved."""
        self._autosave.stop()
        values = self.values()
        notes = []
        if not values["model"] or not self._installed(values["model"]):
            if values["model"]:
                notes.append(f"Ollama doesn't have '{values['model']}', so the model wasn't changed "
                             f"(install it with: ollama pull {values['model']})")
            values["model"] = self.backend.preferences.get("model", settings.DEFAULT_MODEL)
        if not values["personality"]:
            values["personality"] = self.backend.preferences.get("personality", "")
            notes.append("The personality can't be empty, so it wasn't changed")
        if not preferences._valid("wake_phrase", values["wake_phrase"]):
            values["wake_phrase"] = self.backend.preferences.get("wake_phrase", settings.WAKE_PHRASE)
            notes.append("The wake phrase must be one to four words, letters only, so it wasn't changed")

        changed = {key: value for key, value in values.items() if self.backend.preferences.get(key) != value}
        mobile_changed = "mobile_enabled" in changed
        wifi_changed = "mobile_wifi" in changed or "mobile_home_networks" in changed
        wake_changed = any(key in changed for key in WAKE_KEYS)
        if changed:
            self.backend.save_preferences(values)
        ok = self._apply_startup() and self._apply_wake(changed=wake_changed)
        if mobile_changed:
            problem = self.backend.set_mobile(values["mobile_enabled"])
            if problem:
                notes.append(problem)
            self._refresh_phone()
        elif wifi_changed and self.backend.mobile is not None:
            self.backend.mobile.set_wifi(values["mobile_wifi"], values["mobile_home_networks"])
            self._refresh_phone_status()
        if notes:
            logger.info("Settings: some changes not saved (%d)", len(notes))
            self._status(". ".join(notes) + ".", problem=True)
        elif ok:
            self._status(f"Saved at {datetime.now():%H:%M:%S}. New settings are in use." if changed or wake_changed
                         else "Everything is saved.")
        return ok and not notes

    def on_hidden(self):
        self._phone_timer.stop()
        if self._autosave.isActive():
            self.auto_save()             # leaving the page: don't lose a change still waiting to be saved
        super().on_hidden()

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
            "voice_synthesiser": self.voice_engine.currentData(),
            "voice_rate": self.voice_rate.value(),
            "voice_volume": self.voice_volume.value() / 100,
            "voice_pitch": self.voice_pitch.value(),
            "voice_input_device": self.voice_device.currentData() or "",
            "voice_barge_in": self.barge_in.isChecked(),
            "speak_reminders": self.speak_reminders.isChecked(),
            "wake_enabled": self.wake_enabled.isChecked(),
            "mobile_enabled": self.mobile_enabled.isChecked(),
            "mobile_wifi": self.mobile_wifi.isChecked(),
            "mobile_home_networks": list(self._home_networks),
            "wake_key": self.wake_key.currentData(),
            "wake_phrase": " ".join(self.wake_phrase.text().lower().split()),
            "wake_sound": self.wake_sound.isChecked(),
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

    def _apply_wake(self, changed: bool = True) -> bool:
        """Starts, reloads or stops the Wake Engine's helper, and its start with Windows."""
        from aurora.wake import launcher

        try:
            if changed and self.wake_enabled.isChecked():
                if not launcher.reload_helper():
                    launcher.start_helper()
            elif changed:
                launcher.stop_helper()
            wanted = self.wake_startup.isChecked() and self.wake_enabled.isChecked()
            if integration.IS_WINDOWS and wanted != launcher.helper_startup_enabled():
                launcher.set_helper_startup(wanted)
            self.wake_startup.setChecked(launcher.helper_startup_enabled())
            return True
        except Exception as error:
            QMessageBox.warning(self, "Wake Engine", f"Couldn't change the Wake Engine: {error}")
            return False

    def save(self):
        """The "Save now" button: saves at once (changes also save themselves)."""
        self.auto_save()

    # --- Phone ------------------------------------------------------------------------

    def _device_store(self):
        if self.backend.mobile is not None:
            return self.backend.mobile.store
        from aurora.mobile.devices import DeviceStore
        return DeviceStore()

    def _refresh_phone_status(self):
        """The status line (USB, Wi-Fi, which network) and the home networks; cheap, refreshed every 5 s."""
        names = ", ".join(self._home_networks)
        self.home_networks_label.setText(f"Home networks: {names}" if names else
                                         "Home networks: none yet. Connect this PC to your home Wi-Fi, then "
                                         "mark it as home.")
        self.clear_home.setEnabled(bool(self._home_networks))
        service = self.backend.mobile
        if service is None:
            self.mobile_status.setText("Off. Your phone can't reach AURORA.")
            return
        state = service.describe()
        usb = {"no adb": "USB: Android Platform Tools (adb) not found, so the cable link can't be set up",
               "phone plugged in": "USB: phone plugged in and linked",
               "no phone": "USB: no phone plugged in"}[state["usb"]]
        if not self.mobile_wifi.isChecked():
            wifi = "Wi-Fi: off"
        elif state["wifi"]:
            wifi = f"Wi-Fi: listening at {state['wifi']} (HTTPS) on \"{state['network']}\""
        elif state["wifi_problem"]:
            wifi = f"Wi-Fi: {state['wifi_problem']}"
        elif state["network"]:
            wifi = f"Wi-Fi: not listening, \"{state['network']}\" isn't marked as home"
        else:
            wifi = "Wi-Fi: not listening (this PC isn't on Wi-Fi)"
        self.mobile_status.setText(f"On.  {usb}.  {wifi}.")

    def _add_home_network(self):
        from aurora.mobile.network import current_ssid

        name = current_ssid()
        if not name:
            QMessageBox.information(self, "Home network", "This PC isn't connected to a Wi-Fi network right now.")
            return
        if name not in self._home_networks:
            if len(self._home_networks) >= 5:
                QMessageBox.information(self, "Home network", "Up to five home networks. Forget them and start again.")
                return
            self._home_networks.append(name)
            self._changed = self.add_home
            self.auto_save()
        self._refresh_phone_status()

    def _clear_home_networks(self):
        self._home_networks.clear()
        self._changed = self.clear_home
        self.auto_save()
        self._refresh_phone_status()

    def _refresh_phone(self):
        from PySide6.QtWidgets import QCheckBox as Check, QWidget

        running = self.backend.mobile is not None
        self._refresh_phone_status()
        clear_layout(self.device_rows)
        if not running and not settings.MOBILE_DATABASE_PATH.is_file():
            self.pair_button.setEnabled(False)
            self.device_rows.addWidget(label("No phone paired yet.", "Faint"))     # nothing to read (or create)
            self.phone_activity.setText("")
            return
        try:
            store = self._device_store()
            devices = store.devices()
            activity = store.recent_log(6)
        except Exception as error:
            self.device_rows.addWidget(label(f"Couldn't read the paired phone: {error}", "Faint"))
            return
        self.pair_button.setEnabled(running and not devices)
        self.pair_button.setToolTip("Only one phone can be paired. Disconnect it to pair another." if devices else
                                    "Shows a one-time code to type in the AURORA app (phone plugged in by USB).")
        if not devices:
            self.device_rows.addWidget(label("No phone paired yet.", "Faint"))
        for device in devices:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            seen = f"last used {device.last_seen:%d %b %H:%M}" if device.last_seen else "not used yet"
            line.addWidget(label(f"📱 {device.name}", None), 1)
            line.addWidget(label(seen, "Faint"))
            memories = Check("Memories")
            memories.setChecked(device.allow_memories)
            memories.setToolTip("Let this phone see what AURORA remembers about you.")
            memories.toggled.connect(lambda on, d=device.id: store.set_permissions(d, memories=on))
            actions = Check("Approve actions")
            actions.setChecked(device.allow_actions)
            actions.setToolTip("Let this phone answer \"yes\" to actions (switching devices, opening apps, "
                               "moving files). Otherwise answer those on the PC.")
            actions.toggled.connect(lambda on, d=device.id: store.set_permissions(d, actions=on))
            remove = QPushButton("Disconnect")
            remove.setObjectName("Danger")
            remove.clicked.connect(lambda _=False, d=device.id: (store.remove(d), self._refresh_phone()))
            for widget in (memories, actions, remove):
                line.addWidget(widget)
            self.device_rows.addWidget(row)
        self.phone_activity.setText(
            "Recent phone activity: " + "; ".join(f"{t:%d %b %H:%M} {d} {w} ({o})" for t, d, w, o in activity)
            if activity else "")

    def _pair_phone(self):
        from aurora.mobile.devices import PairingError

        if self.backend.mobile is None:
            return
        try:
            code = self.backend.mobile.store.new_code()
        except PairingError as error:
            QMessageBox.information(self, "Pair my phone", str(error))
            return
        shown = f"{code[:4]} {code[4:]}"
        box = QMessageBox(self)
        box.setWindowTitle("Pair my phone")
        box.setText(f"<p>Plug your phone in with the USB cable, open the AURORA app and type this code:</p>"
                    f"<p style='font-size:22pt; letter-spacing:4px'><b>{shown}</b></p>"
                    f"<p>It works once, for 10 minutes, and only over the cable. "
                    f"Close this when the phone says it's paired.</p>")
        box.exec()
        self.backend.mobile.store.cancel_code()
        self._refresh_phone()
    def on_shown(self):
        self._refresh_phone()
        self._phone_timer.start()
        self.model.setCurrentText(settings.DEFAULT_MODEL)
