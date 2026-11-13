import importlib.util
import os

import pytest

from aurora.brain.brain import Brain
from aurora.config import settings
from aurora.kernel.engine import AuroraEngine
from aurora.kernel.permissions import PermissionManager
from aurora.voice.listener import (
    AudioSource, VoiceUnavailable, apply_corrections, numbers_to_digits, strip_wake_word,
)
from aurora.voice.session import VoiceSession
from aurora.voice.speaker import speech_text

# --- Text for speaking ----------------------------------------------------------

def test_speech_text_reads_weather_naturally():
    reply = (
        "Pune, Maharashtra, India, now: 27°C (feels like 29°C), partly cloudy, "
        "humidity 60%, wind 8 km/h.\nToday: 19–31°C.\n(Source: Open-Meteo)"
    )

    assert speech_text(reply) == (
        "Pune, Maharashtra, India, now: 27 degrees Celsius (feels like 29 degrees Celsius), "
        "partly cloudy, humidity 60 percent, wind 8 kilometres per hour. "
        "Today: 19 to 31 degrees Celsius."
    )


def test_speech_text_drops_links_and_bullets():
    reply = "Black hole: A region of spacetime.\nSource: Wikipedia, \"Black hole\" (https://en.wikipedia.org/wiki/Black_hole)"

    assert speech_text(reply) == "Black hole: A region of spacetime."


def test_long_replies_are_cut_with_a_pointer_to_the_screen():
    reply = "First sentence here. " * 50

    spoken = speech_text(reply, max_chars=100)

    assert spoken.endswith("The rest is on screen.")
    assert len(spoken) < 140


# --- Transcript clean-up --------------------------------------------------------

@pytest.mark.parametrize(
    "heard, wanted",
    [
        ("aurora what is the weather", "what is the weather"),
        ("or aura what is the weather", "what is the weather"),
        ("hey or aura remember that i like tea", "remember that i like tea"),
        ("okay aurora", ""),
        ("what is the weather", "what is the weather"),
    ],
)
def test_strip_wake_word(heard, wanted):
    assert strip_wake_word(heard) == wanted


@pytest.mark.parametrize(
    "heard, text",
    [
        ("what is twelve times seven", "what is 12 times 7"),
        ("one hundred and twenty plus five", "120 plus 5"),
        ("two thousand twenty six minus nineteen", "2026 minus 19"),
        ("three point five times two", "3.5 times 2"),
        ("what is twelve time seven", "what is 12 times 7"),     # Misheard "times".
        ("what time is it", "what time is it"),
        ("which one is it", "which one is it"),                  # No arithmetic: unchanged.
        ("i have two cats and one dog", "i have two cats and one dog"),
        # Spoken lists for the Numbers tool: separate numbers stay separate.
        ("what is the average of four eight and fifteen", "what is the average of 4 8 and 15"),
        ("what is the sum of twenty and thirty five", "what is the sum of 20 and 35"),
        ("median of three one nine four", "median of 3 1 9 4"),
        ("total of one hundred five and twelve", "total of 105 and 12"),
        ("average of twelve thirteen and fourteen", "average of 12 13 and 14"),
        ("average of three point five and two", "average of 3.5 and 2"),
    ],
)
def test_numbers_to_digits(heard, text):
    assert numbers_to_digits(heard) == text


def test_user_corrections(monkeypatch):
    monkeypatch.setattr(settings, "VOICE_CORRECTIONS", {"pony": "Pune"})

    assert apply_corrections("what is the weather in pony tomorrow") == "what is the weather in Pune tomorrow"


# --- Voice session with fakes ---------------------------------------------------

class FakeMicrophone(AudioSource):
    opened = 0
    open_now = 0

    def __init__(self):
        FakeMicrophone.opened += 1
        FakeMicrophone.open_now += 1

    def close(self):
        FakeMicrophone.open_now -= 1


class FakeListener:
    """Plays back scripted results instead of real audio."""

    def __init__(self, once=(), wakes=()):
        self.once = list(once)
        self.wakes = list(wakes)

    def listen_once(self, source):
        assert FakeMicrophone.open_now == 1   # The microphone is open while listening.
        return self.once.pop(0) if self.once else ""

    def wait_for_wake_word(self, source, should_stop=lambda: False):
        assert FakeMicrophone.open_now == 1
        return self.wakes.pop(0) if self.wakes else None


class FakeSpeaker:
    def __init__(self):
        self.said = []

    def speak(self, text, should_stop=lambda: False):
        assert FakeMicrophone.open_now == 0   # Never speaking into an open microphone.
        self.said.append(text)
        return True


@pytest.fixture
def engine(fake_provider, memory):
    from aurora.tool_plugins.registry import default_tools
    from test_weather import client_returning

    # Fake HTTP client: tests never reach Wikipedia or any other service.
    engine = AuroraEngine(Brain(fake_provider), memory=memory,
                          tools=default_tools(memory, client_returning()))
    engine.start()
    yield engine
    engine.shutdown()


def session(engine, listener, speaker=None, lines=None):
    FakeMicrophone.opened = FakeMicrophone.open_now = 0
    out = lines if lines is not None else []
    return VoiceSession(engine, listener, speaker or FakeSpeaker(), output=out.append,
                        microphone=FakeMicrophone)


def test_listen_once_answers_and_speaks(engine):
    speaker, lines = FakeSpeaker(), []

    session(engine, FakeListener(once=["what is 12 times 7"]), speaker, lines).listen_once()

    assert "You (voice): what is 12 times 7" in lines
    assert "\nAURORA: 12 * 7 = 84\n" in lines
    assert speaker.said == ["12 * 7 = 84"]
    assert FakeMicrophone.open_now == 0


def test_listen_once_with_silence(engine):
    speaker, lines = FakeSpeaker(), []

    session(engine, FakeListener(once=[""]), speaker, lines).listen_once()

    assert "AURORA: I didn't hear anything.\n" in lines
    assert speaker.said == []


def test_hands_free_request_in_one_breath(engine):
    speaker = FakeSpeaker()

    session(engine, FakeListener(wakes=["what is 2 plus 2", "stop listening"]), speaker).run_hands_free()

    assert speaker.said == ["2 + 2 = 4", "Okay, I've stopped listening."]


def test_hands_free_wake_word_then_request(engine):
    speaker = FakeSpeaker()

    session(engine, FakeListener(wakes=["", "stop"], once=["what is 3 times 3"]), speaker).run_hands_free()

    assert speaker.said == ["Yes?", "3 * 3 = 9", "Okay, I've stopped listening."]


def test_hands_free_ends_when_audio_ends(engine):
    lines = []

    session(engine, FakeListener(wakes=[]), lines=lines).run_hands_free()

    assert lines[-1].startswith("🎙  Hands-free off.")
    assert FakeMicrophone.open_now == 0


def test_microphone_needs_permission(fake_provider, memory):
    engine = AuroraEngine(Brain(fake_provider), permissions=PermissionManager(), memory=memory, tools=[])
    engine.start()

    with pytest.raises(VoiceUnavailable, match="not allowed to use the microphone"):
        session(engine, FakeListener(once=["hello"])).listen_once()

    assert FakeMicrophone.opened == 0


def test_missing_model_is_explained(tmp_path):
    from aurora.voice.listener import Listener

    if importlib.util.find_spec("vosk") is None:
        pytest.skip("vosk not installed")

    with pytest.raises(VoiceUnavailable, match="speech model isn't in"):
        Listener(tmp_path / "no-model")


# --- Real speech: Windows text-to-speech into Vosk (no microphone) --------------

def _real_voice_available():
    return (
        os.name == "nt"
        and importlib.util.find_spec("vosk") is not None
        and importlib.util.find_spec("comtypes") is not None
        and (settings.VOICE_MODEL_DIR / "am").is_dir()
    )


@pytest.fixture(scope="module")
def spoken(tmp_path_factory):
    """WAV files of Windows reading phrases aloud."""
    if not _real_voice_available():
        pytest.skip("Vosk, its model or Windows text-to-speech not available")

    from aurora.voice.speaker import SapiSpeaker

    folder = tmp_path_factory.mktemp("speech")
    phrases = {
        "wake_request": "Aurora, what is twelve times seven?",
        "wake_only": "Hey Aurora.",
        "no_wake": "Remember that my exam is on Friday.",
    }
    speaker = SapiSpeaker(voice="")
    for name, text in phrases.items():
        speaker.save_to_file(text, folder / f"{name}.wav")
    return folder


@pytest.fixture(scope="module")
def listener():
    if not _real_voice_available():
        pytest.skip("Vosk or its model not available")
    from aurora.voice.listener import Listener

    return Listener()


def test_real_transcription(spoken, listener):
    from aurora.voice.listener import WavSource

    assert listener.listen_once(WavSource(spoken / "no_wake.wav")) == "remember that my exam is on friday"


def test_real_partial_words_grow_while_speaking(spoken, listener):
    from aurora.voice.listener import WavSource

    partials = []
    text = listener.listen_once(WavSource(spoken / "no_wake.wav"), on_partial=partials.append)
    assert text == "remember that my exam is on friday"
    assert partials and partials[0].split()[0] == "remember"
    assert all(p for p in partials) and text.startswith(partials[-1].split()[0])


def test_real_pitch_changes_the_voice_not_the_words(listener, tmp_path):
    if not _real_voice_available():
        pytest.skip("Vosk, its model or Windows text-to-speech not available")
    from aurora.voice.listener import WavSource
    from aurora.voice.speaker import SapiSpeaker

    speaker = SapiSpeaker(pitch=6)
    speaker.save_to_file("Remember that my exam is on Friday & bring <pens>.", tmp_path / "high.wav")
    heard = listener.listen_once(WavSource(tmp_path / "high.wav"))
    assert heard.startswith("remember that my exam is on friday")
    assert "pitch" not in heard and "absmiddle" not in heard          # the markup isn't read out


def test_real_wake_word_with_request(spoken, listener):
    from aurora.voice.listener import WavSource

    assert listener.wait_for_wake_word(WavSource(spoken / "wake_request.wav")) == "what is 12 times 7"


def test_real_wake_word_alone(spoken, listener):
    from aurora.voice.listener import WavSource

    assert listener.wait_for_wake_word(WavSource(spoken / "wake_only.wav")) == ""


def test_real_speech_without_wake_word_is_ignored(spoken, listener):
    from aurora.voice.listener import WavSource

    assert listener.wait_for_wake_word(WavSource(spoken / "no_wake.wav")) is None


def test_real_spoken_question_end_to_end(spoken, listener, engine):
    from aurora.voice.listener import WavSource

    class Silence(AudioSource):
        def chunks(self):
            return iter(())

    # First opening plays the recording; after that the "microphone" is silent,
    # so the hands-free loop ends instead of hearing the same request forever.
    openings = iter([WavSource(spoken / "wake_request.wav")])

    FakeMicrophone.open_now = 0
    speaker, lines = FakeSpeaker(), []
    voice = VoiceSession(engine, listener, speaker, output=lines.append,
                         microphone=lambda: next(openings, Silence()))

    voice.run_hands_free()

    # Heard "Aurora, what is twelve times seven?", answered by the calculator.
    assert "\nAURORA: 12 * 7 = 84\n" in lines
    assert speaker.said[0] == "12 * 7 = 84"
