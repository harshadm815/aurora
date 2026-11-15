"""
Phone: the AURORA app on your Android phone, on a page of its own.

  - Connection: on/off, home Wi-Fi and its networks, and how the phone can
    reach AURORA right now (USB cable, home Wi-Fi, Tailscale away from home)
  - Your phone: pairing (one phone, over the cable), its permissions,
    Disconnect
  - Do: call the phone, send it a file
  - Recent activity: which part of AURORA the phone used, never what was said

Changes save themselves at once (aurora_preferences.json), as in Settings.
How it all works: Docs/ANDROID_APP.md.
"""

import logging
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

from aurora.config import settings
from aurora.interface.components.base_view import BaseView
from aurora.interface.components.widgets import Panel, clear_layout, label

logger = logging.getLogger(__name__)

REFRESH_MS = 5000


class PhoneView(BaseView):
    title = "Phone"
    subtitle = "The AURORA app on your Android phone: chat, today, reminders, calls and files."
    nav_icon = "📱"

    def __init__(self, backend, parent=None):
        super().__init__(backend, parent)
        values = backend.preferences
        self.save_status = label("Changes are saved automatically.", "Faint")
        self.body.addWidget(self.save_status)

        # --- Connection
        connection = Panel("Connection")
        self.mobile_enabled = QCheckBox("Let my phone use AURORA")
        self.mobile_enabled.setChecked(values["mobile_enabled"])
        self.mobile_enabled.setToolTip("Listens on this PC only (127.0.0.1), reached over the USB cable, plus your "
                                       "home Wi-Fi if you tick it below. Only your paired phone can use it.")
        self.mobile_enabled.toggled.connect(lambda on: self._save(mobile_enabled=on))
        connection.add(self.mobile_enabled)
        self.mobile_wifi = QCheckBox("Also connect over Wi-Fi, on my home networks only")
        self.mobile_wifi.setChecked(values["mobile_wifi"])
        self.mobile_wifi.setToolTip("Encrypted (HTTPS with a certificate your phone checks), and only while this "
                                    "PC is connected to a network listed below. Elsewhere AURORA doesn't listen "
                                    "on Wi-Fi at all.")
        self.mobile_wifi.toggled.connect(lambda on: self._save(mobile_wifi=on))
        connection.add(self.mobile_wifi)
        self.mobile_launcher = QCheckBox("Let my phone start AURORA when it's closed")
        self.mobile_launcher.setChecked(values.get("mobile_launcher", False))
        self.mobile_launcher.setToolTip("A small helper (no window) runs while AURORA is closed, from Windows sign-in "
                                        "on. It answers only your paired phone, and only to start AURORA (in the "
                                        "notification area). Then the app's Call button works even when AURORA "
                                        "isn't open. It can't wake a PC that is asleep or off.")
        self.mobile_launcher.toggled.connect(self._set_launcher)
        connection.add(self.mobile_launcher)
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
        connection.add_layout(home_row)
        self.mobile_status = connection.add(label("", None, wrap=True))
        away_row = QHBoxLayout()
        self.away_button = QPushButton("Set up away from home")
        self.away_button.setToolTip("Runs \"tailscale serve --bg\" for AURORA: only your own Tailscale devices can "
                                    "reach it (never the public internet), and the phone still signs everything.")
        self.away_button.clicked.connect(self._set_up_away)
        self.away_button.setVisible(False)
        away_row.addWidget(self.away_button)
        away_row.addStretch(1)
        connection.add_layout(away_row)
        self.body.addWidget(connection)

        # --- Your phone
        yours = Panel("Your phone")
        self.device_rows = QVBoxLayout()
        self.device_rows.setSpacing(4)
        yours.add_layout(self.device_rows)
        pair_row = QHBoxLayout()
        self.pair_button = QPushButton("Pair my phone…")
        self.pair_button.clicked.connect(self._pair_phone)
        pair_row.addWidget(self.pair_button)
        pair_row.addStretch(1)
        yours.add_layout(pair_row)
        yours.add(label(
            "Only one phone can be paired, and only over the USB cable: plug it in (USB debugging on), open the "
            "AURORA app and type the code from \"Pair my phone…\". The phone keeps a key in its secure hardware "
            "and signs everything it sends, so nothing else can pretend to be it.", "Faint", wrap=True))
        self.body.addWidget(yours)

        # --- Do
        do = Panel("Do")
        do_row = QHBoxLayout()
        self.call_button = QPushButton("📞 Call my phone")
        self.call_button.setToolTip("AURORA rings your phone; answer it to talk (the phone must be connected).")
        self.call_button.clicked.connect(self._call_phone)
        do_row.addWidget(self.call_button)
        self.send_file_button = QPushButton("📎 Send a file to my phone…")
        self.send_file_button.setToolTip("The phone saves it in Downloads › AURORA. Files you share to AURORA "
                                         "from the phone land in Documents › From phone.")
        self.send_file_button.clicked.connect(self._send_file_to_phone)
        do_row.addWidget(self.send_file_button)
        do_row.addStretch(1)
        do.add_layout(do_row)
        do.add(label("Share anything to \"AURORA (PC)\" on the phone and it's saved in Documents › From phone "
                     "(never over an existing file).", "Faint", wrap=True))
        self.body.addWidget(do)

        # --- Recent activity
        activity = Panel("Recent activity")
        self.phone_activity = activity.add(label("", "Faint", wrap=True))
        self.body.addWidget(activity)

        self.body.addWidget(label(
            "Away from home, Tailscale (free, tailscale.com) carries the connection: install it on the PC and the "
            "phone, sign in to the same account, then press \"Set up away from home\". AURORA must be open on "
            "the PC for the phone to reach it.", "Faint", wrap=True))
        self.body.addStretch(1)

        backend.mobile_changed.connect(lambda _: self._refresh_phone())
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh_phone_status)
        self._refresh_phone()

    # --- Saving -----------------------------------------------------------------------

    def _save(self, **changes) -> None:
        """Saves these phone settings at once and applies them."""
        changed = {key: value for key, value in changes.items() if self.backend.preferences.get(key) != value}
        if not changed:
            return
        self.backend.save_preferences(changed)
        problem = ""
        if "mobile_enabled" in changed:
            problem = self.backend.set_mobile(changed["mobile_enabled"])
            self._refresh_phone()
        elif self.backend.mobile is not None and ("mobile_wifi" in changed or "mobile_home_networks" in changed):
            self.backend.mobile.set_wifi(self.backend.preferences["mobile_wifi"],
                                         self.backend.preferences["mobile_home_networks"])
            self._refresh_phone_status()
        self._status(problem or f"Saved at {datetime.now():%H:%M:%S}.", problem=bool(problem))

    def _set_launcher(self, on: bool) -> None:
        self._save(mobile_launcher=on)
        try:
            from aurora.mobile import launcher
            launcher.set_helper_startup(on)
        except Exception as error:
            self._status(f"Saved, but Windows sign-in couldn't be changed: {error}", problem=True)

    def _status(self, text: str, problem: bool = False):
        self.save_status.setText(("⚠  " if problem else "✓  ") + text)
        self.save_status.setObjectName("Warning" if problem else "Saved")
        self.save_status.style().unpolish(self.save_status)
        self.save_status.style().polish(self.save_status)

    # --- Connection -------------------------------------------------------------------

    def _device_store(self):
        if self.backend.mobile is not None:
            return self.backend.mobile.store
        from aurora.mobile.devices import DeviceStore
        return DeviceStore()

    def _refresh_phone_status(self):
        """How the phone can reach AURORA now, one line per route; cheap, refreshed every few seconds."""
        names = ", ".join(self._home_networks)
        self.home_networks_label.setText(f"Home networks: {names}" if names else
                                         "Home networks: none yet. Connect this PC to your home Wi-Fi, then "
                                         "mark it as home.")
        self.clear_home.setEnabled(bool(self._home_networks))
        service = self.backend.mobile
        if service is None:
            self.mobile_status.setText("Off. Your phone can't reach AURORA.")
            self.away_button.setVisible(False)
            return
        state = service.describe()
        usb = {"no adb": "Android Platform Tools (adb) not found, so the cable link can't be set up",
               "phone plugged in": "phone plugged in and linked",
               "no phone": "no phone plugged in"}[state["usb"]]
        if not self.mobile_wifi.isChecked():
            wifi = "off"
        elif state["wifi"]:
            wifi = f"listening at {state['wifi']} (HTTPS) on \"{state['network']}\""
        elif state["wifi_problem"]:
            wifi = state["wifi_problem"]
        elif state["network"]:
            wifi = f"not listening, \"{state['network']}\" isn't marked as home"
        else:
            wifi = "not listening (this PC isn't on Wi-Fi)"
        away = {"not installed": "Tailscale isn't installed",
                "not connected": "Tailscale is installed but not connected",
                "not set up": "Tailscale is connected; press \"Set up away from home\""}.get(
            state["tailscale"], f"through Tailscale at {state['tailscale']}")
        self.mobile_status.setText(f"On.\n🔌  USB: {usb}\n📶  Wi-Fi: {wifi}\n🌍  Away from home: {away}")
        self.away_button.setVisible(state["tailscale"] == "not set up")

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
            self._save(mobile_home_networks=list(self._home_networks))
        self._refresh_phone_status()

    def _clear_home_networks(self):
        self._home_networks.clear()
        self._save(mobile_home_networks=[])
        self._refresh_phone_status()

    def _set_up_away(self):
        service = self.backend.mobile
        if service is None:
            return
        problem = service.tailscale.serve()
        if problem:
            QMessageBox.information(self, "Away from home", problem)
        else:
            self._status("Away from home is set up. Open the AURORA app once at home so it learns the address.")
        self._refresh_phone_status()

    # --- Your phone -------------------------------------------------------------------

    def _refresh_phone(self):
        running = self.backend.mobile is not None
        self._refresh_phone_status()
        clear_layout(self.device_rows)
        if not running and not settings.MOBILE_DATABASE_PATH.is_file():
            self.pair_button.setEnabled(False)
            self.call_button.setEnabled(False)
            self.send_file_button.setEnabled(False)
            self.device_rows.addWidget(label("No phone paired yet.", "Faint"))     # nothing to read (or create)
            self.phone_activity.setText("")
            return
        try:
            store = self._device_store()
            devices = store.devices()
            activity = store.recent_log(8)
        except Exception as error:
            self.device_rows.addWidget(label(f"Couldn't read the paired phone: {error}", "Faint"))
            return
        self.pair_button.setEnabled(running and not devices)
        self.call_button.setEnabled(running and bool(devices))
        self.send_file_button.setEnabled(running and bool(devices))
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
            memories = QCheckBox("Memories")
            memories.setChecked(device.allow_memories)
            memories.setToolTip("Let this phone see what AURORA remembers about you.")
            memories.toggled.connect(lambda on, d=device.id: store.set_permissions(d, memories=on))
            actions = QCheckBox("Approve actions")
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
            "\n".join(f"{t:%d %b %H:%M}  {d}  {w} ({o})" for t, d, w, o in activity)
            if activity else "Nothing yet.")

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

    # --- Do ---------------------------------------------------------------------------

    def _call_phone(self):
        if self.backend.mobile is not None:
            self.backend.mobile.call_phone()
            self._status("Calling your phone… (it rings if the AURORA app is connected)")

    def _send_file_to_phone(self):
        from PySide6.QtWidgets import QFileDialog
        from aurora.mobile.files import FileError

        if self.backend.mobile is None:
            return
        start = str(settings.DOCUMENTS_DIR) if settings.DOCUMENTS_DIR.is_dir() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Send a file to my phone", start)
        if not path:
            return
        try:
            name = self.backend.mobile.send_file(path)
        except (FileError, OSError) as error:
            QMessageBox.information(self, "Send a file to my phone", str(error))
            return
        self._status(f"Sending {name} to your phone… (it arrives in Downloads › AURORA while the app is connected)")

    # --- Page -------------------------------------------------------------------------

    def on_shown(self):
        self._refresh_phone()
        self._timer.start()
        super().on_shown()

    def on_hidden(self):
        self._timer.stop()
        super().on_hidden()
