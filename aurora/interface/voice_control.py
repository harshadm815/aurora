"""
Voice in the Command Centre: listen (push to talk), speak replies, stop.

All speech work runs on ONE permanent worker thread: Windows' speech
objects (SAPI, COM) must stay on the thread that created them, and the
Vosk model (slow to load from a USB drive) is loaded once and kept.
Results and the microphone level are delivered on the GUI thread.

The microphone is opened only while the user is asking something (the
Chat page's microphone button) or testing it in Settings, and only if
the Voice component has the 'microphone' permission. While AURORA
speaks it stays closed unless barge-in is switched on (headphones).
"""

import logging
import queue
import threading

from PySide6.QtCore import QObject, Signal

from aurora.config import settings
from aurora.voice.session import COMPONENT, PERMISSIONS, BargeIn

logger = logging.getLogger(__name__)

OFF, LOADING, READY, LISTENING, SPEAKING, UNAVAILABLE = "off", "loading", "ready", "listening", "speaking", "unavailable"


def _default_listener():
    from aurora.voice.listener import Listener
    return Listener()


def _default_speaker():
    from aurora.voice.speaker import SapiSpeaker
    return SapiSpeaker()


def _default_microphone():
    from aurora.voice.listener import MicrophoneSource
    return MicrophoneSource()


class VoiceController(QObject):

    state_changed = Signal(str)
    level = Signal(float, bool)          # loudness, speech detected

    def __init__(self, backend, listener_factory=None, speaker_factory=None, microphone=None):
        super().__init__(backend)
        self.backend = backend
        self.listener_factory = listener_factory or _default_listener
        self.speaker_factory = speaker_factory or _default_speaker
        self.microphone = microphone or _default_microphone
        self.state = OFF
        self.problem = ""                 # why voice is unavailable, for the user
        self.listener = None
        self.speaker = None
        self._jobs: queue.Queue = queue.Queue()
        self._stop_listening = threading.Event()
        self._thread = threading.Thread(target=self._work, name="aurora-voice", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------- worker

    def _work(self):
        while True:
            job = self._jobs.get()
            if job is None:
                break
            function, on_result, on_error = job
            try:
                value = function()
            except Exception as error:
                logger.warning("Voice job failed: %s", type(error).__name__)
                if on_error is not None:
                    self.backend.on_gui(on_error, error)
                continue
            if on_result is not None:
                self.backend.on_gui(on_result, value)
        if self.speaker is not None:
            self.speaker = None                # released on its own thread

    def _submit(self, function, on_result=None, on_error=None):
        self._jobs.put((function, on_result, on_error))

    def _set_state(self, state: str):
        self.state = state
        self.backend.on_gui(self.state_changed.emit, state)

    # ------------------------------------------------------------ loading

    def _ensure_speaker(self):
        if self.speaker is None:
            self.speaker = self.speaker_factory()
        return self.speaker

    def _ensure_listener(self):
        if self.listener is None:
            self._set_state(LOADING)
            self.listener = self.listener_factory()
            self._set_state(READY)
        return self.listener

    def preload(self, on_done=None):
        """Load the speech model in the background (it can take a while)."""
        def work():
            try:
                self._ensure_listener()
            except Exception as error:
                self.problem = str(error) or type(error).__name__
                self._set_state(UNAVAILABLE)
                raise
            return True
        self._submit(work, on_done, lambda error: None)

    # ------------------------------------------------------------ actions

    def _permitted(self) -> bool:
        engine = self.backend.engine
        return engine is not None and not engine.permissions.missing(COMPONENT, PERMISSIONS)

    def listen(self, on_heard, on_error) -> None:
        """One question from the microphone; ``on_heard(text)`` ("" if nothing was said)."""
        if not self._permitted():
            on_error(PermissionError("I'm not allowed to use the microphone. Grant 'microphone' to 'Voice' "
                                     "in PERMISSION_GRANTS in aurora/config/settings.py."))
            return
        self._stop_listening.clear()

        def work():
            listener = self._ensure_listener()
            self._set_state(LISTENING)
            try:
                with self.microphone() as source:
                    return listener.listen_once(
                        source, on_level=lambda rms, speaking: self.backend.on_gui(
                            lambda _: self.level.emit(rms, speaking)),
                        should_stop=self._stop_listening.is_set)
            finally:
                self._set_state(READY)

        def failed(error):
            on_error(error)

        self._submit(work, on_heard, failed)

    def speak(self, text: str, on_done=None) -> None:
        """Say ``text``; ``on_done(finished)`` with False if it was interrupted."""
        def work():
            speaker = self._ensure_speaker()
            self._set_state(SPEAKING)
            try:
                if settings.VOICE_BARGE_IN and self._permitted():
                    with BargeIn(self.microphone) as monitor:
                        return speaker.speak(text, monitor.heard.is_set)
                return speaker.speak(text)
            finally:
                self._set_state(READY if self.listener is not None else OFF)

        self._submit(work, on_done, lambda error: logger.warning("Speaking failed"))

    def stop(self) -> None:
        """Stop listening and speaking now (safe from any thread)."""
        self._stop_listening.set()
        if self.speaker is not None:
            self.speaker.stop()

    def apply_settings(self) -> None:
        """Voice, speed and volume from settings (after the Settings page saved them)."""
        def work():
            if self.speaker is not None:
                self.speaker.set_voice(settings.VOICE_NAME)
                self.speaker.set_rate(settings.VOICE_RATE)
                self.speaker.set_volume(settings.VOICE_VOLUME)
        self._submit(work)

    def voices(self, on_result) -> None:
        self._submit(lambda: self._ensure_speaker().voices(), on_result, lambda error: on_result([]))

    def shutdown(self, timeout: float = 5.0) -> None:
        self.stop()
        self._jobs.put(None)
        self._thread.join(timeout)


def input_devices() -> list[str]:
    """Names of the microphones Windows offers (no audio is opened)."""
    try:
        import sounddevice
        devices = sounddevice.query_devices()
    except Exception:
        return []
    names = []
    for device in devices:
        if device.get("max_input_channels", 0) > 0 and device["name"] not in names:
            names.append(device["name"])
    return names
