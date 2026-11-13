"""
The Piper voice (aurora/voice/piper_voice.py) and falling back to Windows'
voice. Audio goes to a fake output, never to the speakers; no microphone,
no network. Tests that need the real voice model skip without it.
"""

import threading
import time

import pytest

from aurora.config import settings
from aurora.voice import providers
from aurora.voice.piper_voice import length_scale


def _piper_available() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return (settings.PIPER_VOICE_DIR / f"{settings.PIPER_VOICE}.onnx").is_file()


class FakeOutput:
    """Collects audio instead of playing it; each write takes as long as real playback would."""

    def __init__(self, rate, realtime=False):
        self.rate, self.realtime, self.written, self.closed = rate, realtime, bytearray(), False

    def write(self, data):
        self.written += data
        if self.realtime:
            time.sleep(len(data) / 2 / self.rate)

    def stop(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture(scope="module")
def piper_voice():
    if not _piper_available():
        pytest.skip("piper-tts or its voice model not available")
    from aurora.voice.piper_voice import PiperSpeaker

    outputs = []
    voice = PiperSpeaker(output=lambda rate: outputs.append(FakeOutput(rate, realtime=voice.realtime)) or outputs[-1])
    voice.realtime = False
    voice.outputs = outputs
    return voice


def test_speed_maps_to_length_scale():
    assert length_scale(170) == pytest.approx(1.0)
    assert length_scale(340) == pytest.approx(0.5) and length_scale(1000) == 0.5     # clamped
    assert length_scale(85) == pytest.approx(2.0) and length_scale(10) == 2.0
    assert length_scale(200) < 1 < length_scale(150)


def test_fallback_to_windows_voice_when_piper_cannot_load(monkeypatch):
    def broken(**options):
        raise RuntimeError("No Piper voice in Models/tts/piper")

    monkeypatch.setitem(providers.SYNTHESISERS, "piper", providers.Provider("piper", "Natural voice", True, broken))
    monkeypatch.setitem(providers.SYNTHESISERS, "sapi", providers.Provider("sapi", "Windows", True, lambda **o: "SAPI"))
    voice, problem = providers.make_synthesiser_or_fallback("piper")
    assert voice == "SAPI" and "isn't available" in problem and "No Piper voice" in problem
    assert providers.make_synthesiser_or_fallback("sapi") == ("SAPI", "")


def test_missing_voice_file_is_explained(tmp_path):
    pytest.importorskip("piper")
    from aurora.voice.piper_voice import PiperSpeaker

    with pytest.raises(RuntimeError, match="No Piper voice"):
        PiperSpeaker(folder=tmp_path)


def test_piper_speaks_every_sentence(piper_voice):
    piper_voice.realtime = False
    assert piper_voice.speak("Hello there. This is the second sentence. And a third one.") is True
    output = piper_voice.outputs[-1]
    seconds = len(output.written) / 2 / output.rate
    assert 3 < seconds < 12 and output.closed


def test_piper_stops_quickly_when_interrupted(piper_voice):
    piper_voice.realtime = True                         # writes take real time, as on a sound card
    text = "This is a long reply. " * 8
    threading.Timer(0.8, piper_voice.stop).start()
    started = time.perf_counter()
    assert piper_voice.speak(text) is False
    took = time.perf_counter() - started
    played = len(piper_voice.outputs[-1].written) / 2 / piper_voice.rate
    assert took < 1.5 and played < 1.5                  # stopped within a block or two, not after 15 s of speech
    piper_voice.realtime = False
    assert piper_voice.speak("Still works after stopping.") is True     # a stop doesn't stick


def test_piper_stops_on_should_stop(piper_voice):
    piper_voice.realtime = True
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 3

    assert piper_voice.speak("One sentence here. Another sentence there.", should_stop) is False
    piper_voice.realtime = False


def test_piper_speech_is_understood(piper_voice, tmp_path):
    pytest.importorskip("vosk")
    if not (settings.VOICE_MODEL_DIR / "am").is_dir():
        pytest.skip("Vosk model not available")
    from aurora.voice.listener import Listener, WavSource

    piper_voice.save_to_file("Remember that my exam is on Friday.", tmp_path / "piper.wav")
    heard = Listener().listen_once(WavSource(tmp_path / "piper.wav"))
    assert heard == "remember that my exam is on friday"


def test_settings_accept_piper_and_its_settings(piper_voice):
    piper_voice.set_rate(220)
    piper_voice.set_volume(2.0)
    piper_voice.set_pitch(5)                             # accepted, no effect
    assert piper_voice.length < 1 and piper_voice.volume == 1.0
    assert settings.PIPER_VOICE in piper_voice.voices() and piper_voice.set_voice("ljspeech")
    piper_voice.set_rate(settings.VOICE_RATE)


def test_controller_switches_voice_engine(qapp_or_skip=None):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from aurora.interface.backend import wait_for
    from aurora.interface.voice_control import VoiceController
    from PySide6.QtCore import QObject

    app = QApplication.instance() or QApplication([])

    class Backend(QObject):
        engine = None

        def on_gui(self, callback, value=None):
            callback(value)

    class Voice:
        def __init__(self, name):
            self.name = name

        def set_voice(self, *_): pass
        def set_rate(self, *_): pass
        def set_volume(self, *_): pass
        def set_pitch(self, *_): pass

    made = []
    backend = Backend()
    controller = VoiceController(backend, listener_factory=lambda: None,
                                 speaker_factory=lambda: made.append(1) or (Voice(settings.VOICE_SYNTHESISER),
                                                                            "Piper isn't available"))
    try:
        controller.preload_speaker()
        assert wait_for(lambda: controller.speaker is not None, 5)
        assert controller.voice_problem == "Piper isn't available"
        old = settings.VOICE_SYNTHESISER
        try:
            settings.VOICE_SYNTHESISER = "sapi" if old == "piper" else "piper"
            controller.apply_settings()
            assert wait_for(lambda: controller.speaker is None, 5)          # reloaded when next needed
        finally:
            settings.VOICE_SYNTHESISER = old
    finally:
        controller.shutdown()
    app.processEvents()
