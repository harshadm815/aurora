"""
Wake Engine (aurora/wake/): the key-hold rules, wake-phrase detection
(real recognition of synthetic speech), false activations, the helper's
flow with a fake keyboard and microphone, and waking the Command Centre.

No real keyboard hook is installed, no microphone or speakers are used,
nothing is launched and no network is touched.
"""

import threading
import time
import wave

import pytest

from aurora.config import settings
from aurora.wake import keys
from aurora.wake.detector import HEARD, NOT_HEARD, UNKNOWN_WORDS, ModelCache, WakeSession, phrase_words
from aurora.wake.keys import VK_RCONTROL, VK_SPACE, HoldKey

VK_A, VK_C = 0x41, 0x43


# --- Key rules: Right Ctrl --------------------------------------------------------------

def test_right_ctrl_hold_starts_and_release_stops():
    m = HoldKey("right_ctrl", 0.45)
    assert m.key_down(VK_RCONTROL, 0.0).swallow is False
    m.tick(0.2)
    assert m.take_actions() == []
    m.key_down(VK_RCONTROL, 0.3)                    # key repeat
    m.tick(0.5)
    assert m.take_actions() == ["start"] and m.state == keys.ACTIVE
    assert m.key_up(VK_RCONTROL, 2.0).swallow is False
    assert m.take_actions() == ["stop"] and m.state == keys.IDLE


def test_right_ctrl_tap_and_shortcuts_never_start():
    m = HoldKey("right_ctrl", 0.45)
    m.key_down(VK_RCONTROL, 0.0)
    m.key_up(VK_RCONTROL, 0.1)
    m.tick(1.0)
    assert m.take_actions() == []
    m.key_down(VK_RCONTROL, 2.0)                    # Right Ctrl + C: a shortcut
    assert m.key_down(VK_C, 2.1).swallow is False
    m.tick(3.0)
    assert m.take_actions() == [] and m.state == keys.SUPPRESSED
    m.key_up(VK_RCONTROL, 3.1)
    assert m.state == keys.IDLE


def test_another_key_while_listening_cancels():
    m = HoldKey("right_ctrl", 0.45)
    m.key_down(VK_RCONTROL, 0.0)
    m.tick(0.5)
    m.key_down(VK_A, 1.0)
    assert m.take_actions() == ["start", "cancel"]
    m.key_up(VK_RCONTROL, 1.5)
    assert m.take_actions() == []                   # no "stop" after a cancel


def test_injected_keys_are_ignored():
    m = HoldKey("right_ctrl", 0.45)
    m.key_down(VK_RCONTROL, 0.0, injected=True)
    m.tick(1.0)
    assert m.state == keys.IDLE and m.take_actions() == []


def test_unknown_key_setting_is_refused():
    with pytest.raises(ValueError):
        HoldKey("caps_lock")


# --- Key rules: Space -------------------------------------------------------------------

def test_space_tap_types_a_space_on_release():
    m = HoldKey("space", 0.45)
    assert m.key_down(VK_SPACE, 0.0).swallow is True
    up = m.key_up(VK_SPACE, 0.1)
    assert up.swallow is True and up.inject == [(VK_SPACE, True), (VK_SPACE, False)]
    assert m.take_actions() == [] and m.state == keys.IDLE


def test_space_hold_starts_without_typing():
    m = HoldKey("space", 0.45)
    m.key_down(VK_SPACE, 0.0)
    assert m.key_down(VK_SPACE, 0.3).swallow is True           # repeats aren't typed
    m.tick(0.5)
    assert m.take_actions() == ["start"]
    assert m.key_down(VK_SPACE, 0.6).swallow is True
    up = m.key_up(VK_SPACE, 2.0)
    assert up.swallow is True and up.inject == [] and m.take_actions() == ["stop"]


def test_fast_typing_keeps_the_order():
    m = HoldKey("space", 0.45)
    m.key_down(VK_SPACE, 0.0)
    nxt = m.key_down(VK_A, 0.05)                                # "a" pressed before Space is released
    assert nxt.swallow is True and nxt.inject == [(VK_SPACE, True), (VK_A, True)]
    assert m.key_down(VK_SPACE, 0.5).swallow is False           # now an ordinary held space: repeats pass
    m.tick(1.0)
    assert m.take_actions() == []
    assert m.key_up(VK_SPACE, 1.1).swallow is False and m.state == keys.IDLE


def test_space_with_modifiers_is_left_alone():
    m = HoldKey("space", 0.45)
    assert m.key_down(VK_SPACE, 0.0, modifiers=True).swallow is False     # Ctrl+Space, Win+Space…
    m.tick(1.0)
    assert m.take_actions() == [] and m.key_up(VK_SPACE, 1.1).swallow is False


def test_space_cancelled_while_listening_types_nothing():
    m = HoldKey("space", 0.45)
    m.key_down(VK_SPACE, 0.0)
    m.tick(0.5)
    m.key_down(VK_A, 1.0)
    assert m.take_actions() == ["start", "cancel"]
    assert m.key_down(VK_SPACE, 1.1).swallow is True
    up = m.key_up(VK_SPACE, 1.2)
    assert up.swallow is True and up.inject == []


# --- Model cache --------------------------------------------------------------------------

def test_model_is_loaded_once_and_freed_when_unused():
    now = [0.0]
    loads = []
    cache = ModelCache(loader=lambda: loads.append(1) or object(), keep_seconds=600, clock=lambda: now[0])
    assert not cache.loaded
    model = cache.get()
    assert cache.get() is model and loads == [1]
    now[0] = 599
    assert not cache.release_if_idle() and cache.loaded
    now[0] = 1300
    assert cache.release_if_idle() and not cache.loaded
    cache.get()
    assert loads == [1, 1]


def test_phrase_words():
    assert phrase_words("  Wake UP! ") == ["wake", "up"]
    assert phrase_words("hey, aurora") == ["hey", "aurora"]


# --- Real recognition of the wake phrase ---------------------------------------------------

def _vosk_ready() -> bool:
    try:
        import vosk  # noqa: F401
    except ImportError:
        return False
    return (settings.VOICE_MODEL_DIR / "am").is_dir()


@pytest.fixture(scope="module")
def cache():
    if not _vosk_ready():
        pytest.skip("Vosk or its model not available")
    return ModelCache(keep_seconds=3600)


def _speak(text: str, path, voice: str) -> tuple[bytes, int]:
    if voice == "piper":
        from aurora.voice.piper_voice import PiperSpeaker
        speaker = PiperSpeaker(output=lambda rate: None)
    else:
        from aurora.voice.speaker import SapiSpeaker
        speaker = SapiSpeaker(voice=voice)
    speaker.save_to_file(text, path)
    with wave.open(str(path), "rb") as file:
        return file.readframes(file.getnframes()), file.getframerate()


def _voices() -> list[str]:
    voices = ["Zira", "David"]
    try:
        import piper  # noqa: F401
        if (settings.PIPER_VOICE_DIR / f"{settings.PIPER_VOICE}.onnx").is_file():
            voices.append("piper")
    except ImportError:
        pass
    return voices


def _detect(cache, audio: bytes, rate: int, phrase: str = "wake up", release: bool = True) -> str:
    results = []
    done = threading.Event()
    session = WakeSession(cache, phrase, rate, lambda r: (results.append(r), done.set()))
    step = rate // 10 * 2
    for start in range(0, len(audio), step):
        session.feed(audio[start:start + step])
    session.feed(b"\x00\x00" * (rate // 2))           # half a second of quiet, as when the speaker stops
    if release:
        session.finish()
    assert done.wait(30)
    return results[0]


@pytest.mark.parametrize("voice", _voices())
def test_wake_phrase_is_heard(cache, tmp_path, voice):
    audio, rate = _speak("Wake up.", tmp_path / "wake.wav", voice)
    assert _detect(cache, audio, rate) == HEARD


@pytest.mark.parametrize("voice", _voices())
def test_false_activations(cache, tmp_path, voice):
    """Other speech while the key is held mustn't wake AURORA. Measured, then held to a limit."""
    others = ["Make up your mind.", "What's up?", "Wait up for me.", "Hello there.", "Take a break.",
              "Open the weather.", "Shake it up.", "Turn on the light.", "Up and away.", "Wake."]
    woken = [text for text in others
             if _detect(cache, *_speak(text, tmp_path / "other.wav", voice)) == HEARD]
    print(f"{voice}: {len(woken)}/{len(others)} false activations: {woken}")
    assert len(woken) <= 1, woken


def test_phrase_said_while_the_model_loads_is_not_lost(tmp_path):
    if not _vosk_ready():
        pytest.skip("Vosk or its model not available")
    from aurora.wake.detector import _load_vosk

    def slow_loader():
        time.sleep(1.5)                                  # as on a cold USB drive (it can be 7 s)
        return _load_vosk()

    audio, rate = _speak("Wake up.", tmp_path / "wake.wav", "Zira")
    assert _detect(ModelCache(loader=slow_loader), audio, rate) == HEARD


def test_silence_and_cancel(cache):
    assert _detect(cache, b"\x00\x00" * 16000, 16000) == NOT_HEARD
    results = []
    session = WakeSession(cache, "wake up", 16000, results.append)
    session.feed(b"\x00\x00" * 1600)
    session.cancel()
    time.sleep(0.5)
    assert results == [] and session.result is None


def test_phrase_with_unknown_words_is_explained(cache):
    results = []
    done = threading.Event()
    session = WakeSession(cache, "wake zxqvbn", 16000, lambda r: (results.append(r), done.set()))
    session.finish()
    assert done.wait(20) and results == [UNKNOWN_WORDS] and session.unknown == ["zxqvbn"]


# --- Waking the Command Centre --------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(app, seconds=0.5, until=lambda: False):
    end = time.time() + seconds
    while time.time() < end and not until():
        app.processEvents()
        time.sleep(0.01)


def test_single_instance_passes_wake_and_show(qapp):
    from aurora.interface.windows.single_instance import SHOW, WAKE, SingleInstance

    name = f"AURORA-test-{time.time_ns()}"
    first = SingleInstance(name)
    assert first.acquire()
    got = []
    first.woken.connect(lambda: got.append("wake"))
    first.activated.connect(lambda: got.append("show"))
    def from_another_program(message):
        # Another process in real use; a thread here, so this one keeps handling events.
        sent = []
        thread = threading.Thread(target=lambda: sent.append(SingleInstance.send(message, name, 2000)))
        thread.start()
        _pump(qapp, 4, lambda: not thread.is_alive())
        return sent == [True]

    try:
        assert from_another_program(WAKE)
        _pump(qapp, 2, lambda: got)
        assert from_another_program(SHOW)
        _pump(qapp, 2, lambda: len(got) == 2)
        assert got == ["wake", "show"]
    finally:
        first.release()
    assert SingleInstance.send(WAKE, name) is False


def test_wake_starts_the_command_centre_when_it_is_not_running(qapp, monkeypatch):
    from aurora.interface.windows import single_instance
    from aurora.wake import launcher

    monkeypatch.setattr(single_instance, "server_name", lambda: f"AURORA-test-none-{time.time_ns()}")
    started = []
    assert launcher.wake_command_centre(start=lambda command, **kw: started.append(command)) == "started"
    assert started and started[0][-1] == "--wake"


def test_helper_command_and_startup_entry():
    from aurora.wake import launcher

    assert launcher.helper_command()[-1] == "--wake-helper"

    class Registry:
        HKEY_CURRENT_USER = object()
        KEY_READ = KEY_SET_VALUE = REG_SZ = 0
        values = {}

        def OpenKey(self, *a):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def SetValueEx(self, key, name, _, kind, value):
            self.values[name] = value

        def QueryValueEx(self, key, name):
            if name not in self.values:
                raise FileNotFoundError(name)
            return self.values[name], 1

        def DeleteValue(self, key, name):
            del self.values[name]

    registry = Registry()
    launcher.set_helper_startup(True, registry)
    assert launcher.helper_startup_enabled(registry) and "--wake-helper" in registry.values[launcher.RUN_VALUE]
    launcher.set_helper_startup(False, registry)
    assert not launcher.helper_startup_enabled(registry)


# --- The helper, with a fake keyboard and microphone ----------------------------------------

class FakeHook:
    def __init__(self, machine, on_event):
        self.machine, self.on_event, self.installed = machine, on_event, False

    def install(self):
        self.installed = True

    def uninstall(self):
        self.installed = False

    def down(self, vk, now):
        self.machine.key_down(vk, now)
        self.on_event()

    def up(self, vk, now):
        self.machine.key_up(vk, now)
        self.on_event()


class FakeMicrophone:
    """Plays prepared audio into the helper while 'open'."""
    audio = b""
    opened = 0

    def __init__(self, on_audio):
        FakeMicrophone.opened += 1
        self.open = True
        step = 3200
        for start in range(0, len(self.audio), step):
            on_audio(self.audio[start:start + step])
        on_audio(b"\x00\x00" * 8000)

    def close(self):
        self.open = False


def _sixteen_khz(audio: bytes, rate: int) -> bytes:
    import numpy as np
    samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
    positions = np.arange(0, len(samples), rate / 16000)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.int16).tobytes()


@pytest.fixture
def helper(qapp, cache, monkeypatch, tmp_path):
    from aurora.config import preferences
    from aurora.wake import helper as helper_module

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    monkeypatch.setattr(helper_module, "microphone_allowed", lambda: True)
    woken = []
    h = helper_module.WakeHelper(hook_factory=FakeHook, microphone_factory=FakeMicrophone,
                                 wake=lambda: woken.append(1), cache=cache, beep=lambda: None)
    h.woken = woken
    yield h
    h.shutdown()
    h.overlay.close()


def _hold(helper, qapp, seconds=0.6):
    helper.hook.down(helper.machine.vk, time.monotonic())
    _pump(qapp, seconds + 0.3, lambda: helper.microphone is not None)


def test_helper_wakes_aurora_on_the_phrase(helper, qapp, tmp_path):
    audio, rate = _speak("Wake up.", tmp_path / "wake.wav", "Zira")
    FakeMicrophone.audio = _sixteen_khz(audio, rate)
    assert helper.hook.installed and helper.idle
    _hold(helper, qapp)
    _pump(qapp, 15, lambda: helper.woken)
    assert helper.woken == [1]
    assert helper.microphone is None                       # closed as soon as the phrase was heard
    assert "Waking AURORA" in helper.overlay.text.text()
    helper.hook.up(helper.machine.vk, time.monotonic())
    _pump(qapp, 0.3)
    assert helper.idle


def test_helper_other_speech_does_not_wake(helper, qapp, tmp_path):
    audio, rate = _speak("Turn on the light.", tmp_path / "other.wav", "Zira")
    FakeMicrophone.audio = _sixteen_khz(audio, rate)
    _hold(helper, qapp)
    helper.hook.up(helper.machine.vk, time.monotonic())        # release: microphone closes, audio is checked
    _pump(qapp, 1, lambda: helper.microphone is None)
    assert helper.microphone is None
    _pump(qapp, 15, lambda: "Didn't hear" in helper.overlay.text.text())
    assert helper.woken == [] and "Didn't hear" in helper.overlay.text.text()


def test_helper_short_tap_never_opens_the_microphone(helper, qapp):
    FakeMicrophone.opened = 0
    helper.hook.down(helper.machine.vk, time.monotonic())
    helper.hook.up(helper.machine.vk, time.monotonic())
    _pump(qapp, 0.8)
    assert FakeMicrophone.opened == 0 and helper.idle and not helper._ticker.isActive()


def test_helper_needs_the_microphone_permission(helper, qapp, monkeypatch):
    from aurora.wake import helper as helper_module

    monkeypatch.setattr(helper_module, "microphone_allowed", lambda: False)
    FakeMicrophone.opened = 0
    _hold(helper, qapp)
    assert FakeMicrophone.opened == 0 and "isn't allowed" in helper.overlay.text.text()
    helper.hook.up(helper.machine.vk, time.monotonic())


def test_microphone_permission_comes_from_settings(monkeypatch):
    from aurora.wake import helper as helper_module

    monkeypatch.setitem(settings.PERMISSION_GRANTS, "Voice", [])
    assert helper_module.microphone_allowed() is False
    monkeypatch.setitem(settings.PERMISSION_GRANTS, "Voice", ["microphone"])
    assert helper_module.microphone_allowed() is True


# --- The Command Centre's side: greet and listen ------------------------------------------

def test_window_wakes_greets_and_listens(qapp, memory, tmp_path, monkeypatch):
    from aurora.brain.brain import Brain
    from aurora.config import preferences
    from aurora.interface.backend import Backend, wait_for
    from aurora.interface.main_window import MainWindow
    from aurora.kernel.engine import AuroraEngine
    from conftest import FakeProvider

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    backend = Backend(engine_factory=lambda: AuroraEngine(Brain(FakeProvider()), memory=memory))
    said, listened = [], []

    def speak(text, on_done=None):
        said.append(text)
        on_done(True)

    window = MainWindow(backend, use_tray=False)
    monkeypatch.setattr(backend.voice, "speak", speak)
    monkeypatch.setattr(window.pages["Chat"], "listen", lambda: listened.append(1))
    try:
        window.wake()                                  # before the engine is ready: waits for it
        assert said == []
        backend.start()
        assert wait_for(lambda: backend.engine is not None, 10)
        _pump(qapp, 3, lambda: said)
        assert said == [settings.WAKE_GREETING] and listened == [1] and window.current == "Chat"
        window.wake()                                  # engine ready: greets at once
        assert said == [settings.WAKE_GREETING] * 2
    finally:
        window.close()
        backend.shutdown()
