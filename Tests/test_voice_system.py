"""Phase 12: voice activity detection, interruptible speech, barge-in, the
Command Centre's voice controls, and the loop-back reliability test."""

import importlib.util
import math
import os
import struct
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import release_widgets

from aurora.config import preferences, settings
from aurora.voice import speaker as speaker_module
from aurora.voice.listener import AudioSource
from aurora.voice.session import BargeIn, VoiceSession
from aurora.voice.speaker import SapiSpeaker, sapi_rate, sentences, speech_text
from aurora.voice.vad import VadSettings, VoiceActivityDetector, rms

RATE = 16000


def tone(seconds, amplitude, rate=RATE, frequency=220.0):
    n = int(rate * seconds)
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * frequency * i / rate)) for i in range(n)))


def silence(seconds, rate=RATE):
    return b"\x00\x00" * int(rate * seconds)


def chunks(audio, size=4000):
    return [audio[i:i + size * 2] for i in range(0, len(audio), size * 2)]


def run_vad(audio, config=None):
    detector = VoiceActivityDetector(RATE, config or VadSettings())
    for chunk in chunks(audio):
        detector.feed(chunk)
        if detector.ended or detector.gave_up:
            break
    return detector


# ============================================================ voice activity detection


def test_rms():
    assert rms(b"") == 0.0
    assert rms(struct.pack("<4h", 100, -100, 100, -100)) == pytest.approx(100)


def test_speech_is_detected_and_ends_after_silence():
    d = run_vad(silence(0.5) + tone(1.0, 5000) + silence(2.0))
    assert d.started and d.ended and not d.gave_up
    assert 0.9 <= d.speech_seconds <= 1.1
    assert d.elapsed < 0.5 + 1.0 + 2.0                       # stopped before the audio ran out


def test_nobody_speaking_gives_up_early():
    d = run_vad(silence(8))
    assert d.gave_up and not d.started
    assert d.elapsed == pytest.approx(5.0, abs=0.3)


def test_a_click_is_not_speech():
    d = run_vad(silence(0.5) + tone(0.04, 20000) + silence(6))
    assert not d.started


def test_the_threshold_follows_a_noisy_room():
    import random
    rng = random.Random(1)
    noise = struct.pack(f"<{RATE}h", *(int(rng.gauss(0, 400)) for _ in range(RATE)))
    d = run_vad(noise + noise)
    assert not d.started and d.threshold > 1000              # the room is learned, not mistaken for speech
    d = run_vad(noise + tone(1.0, 9000) + silence(2))
    assert d.started and d.ended


def test_a_room_louder_than_the_minimum_is_learned_not_mistaken_for_speech():
    hum = tone(3.0, 900, frequency=50.0)                     # a steady hum: rms ~640, above minimum_rms
    d = run_vad(hum + hum)
    assert not d.started and d.gave_up


def test_empty_chunks_count_as_a_second_of_nothing():
    d = VoiceActivityDetector(RATE, VadSettings(no_speech_timeout=3))
    for _ in range(3):
        d.feed(b"")
    assert d.gave_up


# ============================================================ speech text


def test_speech_text_leaves_out_citations_and_sources():
    reply = ("From your documents:\n  “The sample contains 42 planets.” [1]\n\nSources:\n  [1] thesis.pdf, page 2")
    assert speech_text(reply) == "From your documents: The sample contains 42 planets."
    assert speech_text("27°C, 19–31°C") == "27 degrees Celsius, 19 to 31 degrees Celsius"
    assert sentences("One. Two? Three!") == ["One.", "Two?", "Three!"]


def test_sapi_rate():
    assert sapi_rate(180) == 0 and sapi_rate(300) == 7 and sapi_rate(1000) == 10 and sapi_rate(20) == -9


# ============================================================ SAPI speaker (fake COM voice)


class FakeVoice:
    """Speaks asynchronously: each utterance takes `ticks` WaitUntilDone polls."""

    def __init__(self, ticks=4):
        self.ticks = ticks
        self.spoken, self.purges = [], 0
        self.remaining = 0
        self.Rate = self.Volume = 0
        self.Voice = None
        self.AudioOutputStream = "speakers"

    def Speak(self, text, flags):
        if flags & 2:
            self.purges += 1
            self.remaining = 0
            return
        self.spoken.append(text)
        self.remaining = self.ticks if flags & 1 else 0

    def WaitUntilDone(self, ms):
        if self.remaining <= 0:
            return True
        time.sleep(0.001)               # speaking takes time, as the real thing does
        self.remaining -= 1
        return False

    def GetVoices(self):
        voices = [type("Token", (), {"GetDescription": lambda self, n=n: n})() for n in
                  ("Microsoft David Desktop", "Microsoft Zira Desktop")]
        return type("Tokens", (), {"Count": 2, "Item": lambda self, i: voices[i]})()


@pytest.fixture
def fake_sapi(monkeypatch):
    import comtypes.client

    voice = FakeVoice()
    streams = []

    class Stream:
        def Open(self, path, mode):
            self.path = path
            streams.append(self)

        def Close(self):
            self.closed = True

    monkeypatch.setattr(comtypes.client, "CreateObject",
                        lambda name: voice if name == "SAPI.SpVoice" else Stream())
    voice.streams = streams
    return voice


pytestmark_sapi = pytest.mark.skipif(importlib.util.find_spec("comtypes") is None, reason="comtypes missing")


@pytestmark_sapi
def test_speaker_says_every_sentence(fake_sapi):
    speaker = SapiSpeaker(rate=200, voice="zira", volume=0.5)
    assert fake_sapi.Rate == 1 and fake_sapi.Volume == 50
    assert fake_sapi.Voice.GetDescription() == "Microsoft Zira Desktop"
    assert speaker.speak("First sentence. Second sentence.") is True
    assert fake_sapi.spoken == ["First sentence.", "Second sentence."] and fake_sapi.purges == 0


@pytestmark_sapi
def test_speaker_can_be_interrupted(fake_sapi):
    speaker = SapiSpeaker(voice="")
    polls = []

    def stop_soon():
        polls.append(1)
        return len(polls) >= 2

    assert speaker.speak("First sentence. Second sentence.", stop_soon) is False
    assert fake_sapi.spoken == ["First sentence."] and fake_sapi.purges == 1


@pytestmark_sapi
def test_stop_works_from_another_thread(fake_sapi):
    fake_sapi.ticks = 10_000
    speaker = SapiSpeaker(voice="")
    result = []
    worker = threading.Thread(target=lambda: result.append(speaker.speak("A very long sentence.")))
    worker.start()
    time.sleep(0.05)
    speaker.stop()
    worker.join(5)
    assert result == [False] and fake_sapi.purges == 1


@pytestmark_sapi
def test_speaker_speaks_again_after_an_interruption(fake_sapi):
    speaker = SapiSpeaker(voice="")
    speaker.stop()
    assert speaker.speak("Hello.") is True                 # each speak starts fresh


@pytestmark_sapi
def test_save_to_file_restores_the_speakers(fake_sapi, tmp_path):
    speaker = SapiSpeaker(voice="")
    speaker.save_to_file("Hello there.", tmp_path / "x.wav")
    assert fake_sapi.streams[0].closed and fake_sapi.AudioOutputStream == "speakers"
    assert speaker.voices() == ["Microsoft David Desktop", "Microsoft Zira Desktop"]


# ============================================================ sessions and barge-in


class LoudMicrophone(AudioSource):
    """A microphone where someone starts talking after `delay` seconds."""

    rate = RATE
    opened = 0

    def __init__(self, delay=0.3):
        LoudMicrophone.opened += 1
        self.audio = silence(delay) + tone(2.0, 9000)

    def chunks(self):
        for chunk in chunks(self.audio, 800):
            time.sleep(0.005)
            yield chunk
        while True:
            time.sleep(0.01)
            yield silence(0.05)


def test_barge_in_hears_the_user():
    with BargeIn(LoudMicrophone) as monitor:
        assert monitor.heard.wait(5)


def test_barge_in_ignores_a_quiet_room():
    class Quiet(LoudMicrophone):
        def __init__(self):
            self.audio = silence(3)

    with BargeIn(Quiet) as monitor:
        assert not monitor.heard.wait(1.0)


def test_barge_in_without_a_microphone_just_doesnt_interrupt():
    def broken():
        raise RuntimeError("no microphone")

    with BargeIn(broken) as monitor:
        time.sleep(0.1)
    assert monitor.error is not None and not monitor.heard.is_set()


class RecordingSpeaker:
    def __init__(self):
        self.calls = []

    def speak(self, text, should_stop=lambda: False):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if should_stop():
                self.calls.append((text, "interrupted"))
                return False
            time.sleep(0.01)
        self.calls.append((text, "finished"))
        return True


def engine_with(permissions):
    from aurora.kernel.permissions import PermissionManager

    return type("Engine", (), {"permissions": PermissionManager(permissions)})()


def test_session_speech_can_be_interrupted_by_a_key():
    pressed = threading.Event()
    speaker = RecordingSpeaker()
    session = VoiceSession(engine_with({}), None, speaker, output=lambda text: None, interrupt=pressed.is_set)
    threading.Timer(0.1, pressed.set).start()
    assert session.say("Hello.") is False and speaker.calls == [("Hello.", "interrupted")]


def test_session_barge_in_stops_speech():
    speaker = RecordingSpeaker()
    session = VoiceSession(engine_with({"Voice": ["microphone"]}), None, speaker, output=lambda text: None,
                           microphone=LoudMicrophone, barge_in=True)
    started = time.monotonic()
    assert session.say("A long reply.") is False
    assert time.monotonic() - started < 3


def test_barge_in_needs_the_microphone_permission():
    speaker = RecordingSpeaker()
    LoudMicrophone.opened = 0
    stop = threading.Event()
    session = VoiceSession(engine_with({}), None, speaker, output=lambda text: None,
                           microphone=LoudMicrophone, barge_in=True, interrupt=stop.is_set)
    threading.Timer(0.2, stop.set).start()
    session.say("Hello.")
    assert LoudMicrophone.opened == 0                       # no permission: the microphone stays closed


def test_barge_in_is_off_by_default():
    assert settings.VOICE_BARGE_IN is False


# ============================================================ near-miss device names


@pytest.mark.parametrize("heard,suggested", [("the desk life", "desk light"), ("desk lite", "desk light"),
                                             ("the ceiling fen", "fan")])
def test_misheard_device_names_are_suggested_and_always_confirmed(heard, suggested, monkeypatch):
    from aurora.tool_plugins.devices import DevicesTool

    devices = [dict(d, confirm=False) for d in settings.DEVICES]      # even devices that normally don't ask
    monkeypatch.setattr(settings, "DEVICES", devices)
    result = DevicesTool().execute(action="on", device=heard)
    assert f"Did you mean the {suggested}?" in result.message
    assert result.needs_confirmation


@pytest.mark.parametrize("heard", ["the fun", "the kettle", "the toaster"])
def test_unrelated_device_names_are_still_refused(heard):
    from aurora.tool_plugins.devices import DevicesTool

    result = DevicesTool().execute(action="on", device=heard)
    assert not result.success and "I don't have a device called" in result.message


# ============================================================ loop-back scoring


def test_loop_back_scoring_with_fakes():
    from aurora.voice.selftest import normalize, run, summary, word_errors

    assert normalize("What is twelve times seven") == ["what", "is", "12", "times", "7"]   # Vosk adds no punctuation
    assert word_errors(["a", "b", "c"], ["a", "x", "c", "d"]) == 2

    class Speaker:
        def save_to_file(self, text, path):
            path.write_text(text)

    class Listener:
        def listen_once(self, source):
            return {"what is twelve times seven": "what is twelve times seven",
                    "turn on the desk light": "turn on the desk life"}.get(source.text, "")

    import aurora.voice.listener as listener_module

    class Source:
        def __init__(self, path):
            self.text = open(path).read()

    original = listener_module.WavSource
    listener_module.WavSource = Source
    try:
        trials = run([("what is twelve times seven", "calculator"), ("turn on the desk light", "devices")],
                     voices=["Test"], listener=Listener(), speaker_factory=lambda voice: Speaker())
    finally:
        listener_module.WavSource = original
    assert [t.correct_command for t in trials] == [True, True]
    assert summary(trials)["word_error_rate"] == pytest.approx(1 / 10)       # "life" for "light", of 10 words


def _real_voice():
    return (os.name == "nt" and importlib.util.find_spec("vosk") is not None
            and importlib.util.find_spec("comtypes") is not None and (settings.VOICE_MODEL_DIR / "am").is_dir())


@pytest.mark.skipif(not _real_voice(), reason="Vosk, its model or Windows text-to-speech not available")
def test_real_loop_back_is_reliable():
    """Windows speaks into a file, Vosk transcribes it: words and commands come back right."""
    from aurora.voice.selftest import run, summary

    trials = run([("what is the average of four eight and fifteen", "numbers"),
                  ("what do my notes say about dark matter", "knowledge_base"),
                  ("set a timer for ten minutes", "reminder")], voices=["Zira"])
    result = summary(trials)
    assert result["command_accuracy"] == 1.0 and result["word_error_rate"] <= 0.1


# ============================================================ preferences


def test_voice_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    values = preferences.defaults()
    assert values["voice_volume"] == 1.0 and values["voice_input_device"] == "" and values["speak_replies"] is False
    values.update({"voice_volume": 1, "voice_barge_in": True, "voice_input_device": "", "speak_replies": True})
    preferences.save(values)
    loaded = preferences.load()
    assert loaded["voice_volume"] == 1.0 and loaded["voice_barge_in"] is True
    monkeypatch.setattr(settings, "VOICE_INPUT_DEVICE", "something")
    preferences.apply(loaded)
    assert settings.VOICE_INPUT_DEVICE is None and settings.VOICE_BARGE_IN is True
    assert not preferences._valid("voice_volume", 1.5) and not preferences._valid("voice_volume", True)


# ============================================================ Command Centre voice


pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from aurora.interface.backend import Backend, wait_for  # noqa: E402
from aurora.interface.voice_control import LISTENING, SPEAKING, VoiceController  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class FakeListener:
    def __init__(self, text="what is 12 times 7"):
        self.text = text
        self.sources = []

    def listen_once(self, source, on_level=None, should_stop=lambda: False, on_partial=None):
        self.sources.append(source)
        if on_level:
            on_level(1200.0, True)
        if on_partial and self.text:
            on_partial(self.text.split()[0])
        return self.text


class FakeSpeaker:
    def __init__(self):
        self.said = []
        self._stop = threading.Event()
        self.settings = []

    def speak(self, text, should_stop=lambda: False):
        self._stop.clear()
        self.said.append(text)
        return True

    def stop(self):
        self._stop.set()

    def set_voice(self, name):
        self.settings.append(("voice", name))

    def set_rate(self, rate):
        self.settings.append(("rate", rate))

    def set_volume(self, volume):
        self.settings.append(("volume", volume))

    def voices(self):
        return ["Fake Voice"]


class FakeMic(AudioSource):
    opened = 0

    def __init__(self):
        FakeMic.opened += 1


@pytest.fixture
def backend(qapp, memory, tmp_path, monkeypatch):
    from aurora.brain.brain import Brain
    from aurora.kernel.engine import AuroraEngine
    from aurora.tool_plugins.registry import default_tools
    from conftest import FakeProvider
    from test_weather import client_returning

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    b = Backend(engine_factory=lambda: AuroraEngine(Brain(FakeProvider()), memory=memory,
                                                    tools=default_tools(memory, client_returning())))
    b.voice.shutdown()
    b.voice = VoiceController(b, listener_factory=FakeListener, speaker_factory=FakeSpeaker, microphone=FakeMic)
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    yield b
    b.shutdown()
    release_widgets()


def test_voice_controller_listens_and_speaks(backend):
    voice = backend.voice
    states, levels, heard = [], [], []
    voice.state_changed.connect(states.append)
    voice.level.connect(lambda rms, speaking: levels.append((rms, speaking)))
    FakeMic.opened = 0
    voice.listen(heard.append, pytest.fail)
    assert wait_for(lambda: heard == ["what is 12 times 7"], 10)
    assert FakeMic.opened == 1 and LISTENING in states and levels == [(1200.0, True)]
    done = []
    voice.speak("Hello.", done.append)
    assert wait_for(lambda: done == [True], 10)
    assert voice.speaker.said == ["Hello."] and SPEAKING in states


def test_voice_controller_needs_the_microphone_permission(qapp, memory, tmp_path, monkeypatch):
    from aurora.brain.brain import Brain
    from aurora.kernel.engine import AuroraEngine
    from aurora.kernel.permissions import PermissionManager
    from conftest import FakeProvider

    monkeypatch.setattr(preferences, "PATH", tmp_path / "prefs.json")
    b = Backend(engine_factory=lambda: AuroraEngine(Brain(FakeProvider()), memory=memory, tools=[],
                                                    permissions=PermissionManager({})))
    b.voice.shutdown()
    b.voice = VoiceController(b, listener_factory=FakeListener, speaker_factory=FakeSpeaker, microphone=FakeMic)
    b.start()
    assert wait_for(lambda: b.engine is not None, 10)
    errors = []
    FakeMic.opened = 0
    b.voice.listen(pytest.fail, errors.append)
    assert errors and "not allowed" in str(errors[0]) and FakeMic.opened == 0
    b.shutdown()


def test_voice_settings_reach_the_speaker(backend):
    backend.voice.speak("x")
    assert wait_for(lambda: backend.voice.speaker is not None and backend.voice.speaker.said, 10)
    backend.save_preferences({"voice_name": "David", "voice_rate": 200, "voice_volume": 0.4})
    assert wait_for(lambda: ("volume", 0.4) in backend.voice.speaker.settings, 10)
    assert ("voice", "David") in backend.voice.speaker.settings


def test_voice_thread_ends_on_shutdown(backend):
    thread = backend.voice._thread
    backend.shutdown()
    assert not thread.is_alive()


def test_chat_asks_by_voice_and_speaks_the_reply(backend):
    from aurora.interface.main_window import MainWindow

    window = MainWindow(backend)
    window.show()
    chat = window.pages["Chat"]
    chat.speak_replies.setChecked(True)
    partials = []
    backend.voice.partial.connect(partials.append)
    chat.listen()
    assert wait_for(lambda: backend.voice.speaker is not None and backend.voice.speaker.said, 15)
    assert partials[:2] == ["", "what"]                 # cleared when listening starts, then the words so far
    assert chat._partial == "what"
    assert backend.voice.speaker.said == ["12 * 7 = 84"]
    bubbles = [w.meta.text() for w in chat.findChildren(type(chat._add_bubble("x", "y")))]
    assert any(text.startswith("You (voice)") for text in bubbles)
    assert backend.preferences["speak_replies"] is True
    window.close()


def test_chat_reports_silence(backend):
    from aurora.interface.main_window import MainWindow

    backend.voice.listener = FakeListener(text="")
    window = MainWindow(backend)
    chat = window.pages["Chat"]
    chat.listen()
    assert wait_for(lambda: "didn't hear anything" in chat.voice_status.text(), 10)
    window.close()
