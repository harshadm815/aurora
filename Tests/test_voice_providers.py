"""
Speech providers (aurora/voice/providers.py) and the SAPI pitch markup.
No microphone, speakers or network.
"""

import pytest

from aurora.config import settings
from aurora.voice import providers, speaker
from aurora.voice.listener import Listener
from aurora.voice.speaker import SapiSpeaker


def test_built_in_providers_are_local_and_implement_the_interface():
    assert providers.RECOGNISERS["vosk"].local and providers.SYNTHESISERS["sapi"].local
    assert Listener.name == "vosk" and Listener.local
    assert SapiSpeaker.name == "sapi" and SapiSpeaker.local
    for method in ("listen_once", "wait_for_wake_word", "transcribe"):
        assert callable(getattr(Listener, method))
    for method in ("speak", "stop"):
        assert callable(getattr(SapiSpeaker, method))


def test_settings_choose_the_provider(monkeypatch):
    made = []
    monkeypatch.setitem(providers.RECOGNISERS, "fake",
                        providers.Provider("fake", "Fake", True, lambda **o: made.append(o) or "R"))
    monkeypatch.setattr(settings, "VOICE_RECOGNISER", "fake")
    assert providers.make_recogniser(model_path="x") == "R" and made == [{"model_path": "x"}]


def test_unknown_provider_is_refused():
    with pytest.raises(providers.ProviderUnavailable, match="Available: vosk"):
        providers.make_recogniser("nonexistent")
    with pytest.raises(providers.ProviderUnavailable):
        providers.make_synthesiser("nonexistent")


def test_external_provider_needs_permission(monkeypatch):
    monkeypatch.setitem(providers.SYNTHESISERS, "cloud",
                        providers.Provider("cloud", "Cloud voice", False, lambda **o: "voice"))
    monkeypatch.setattr(settings, "VOICE_ALLOW_EXTERNAL", False)
    with pytest.raises(providers.ProviderUnavailable, match="outside this PC"):
        providers.make_synthesiser("cloud")
    monkeypatch.setattr(settings, "VOICE_ALLOW_EXTERNAL", True)
    assert providers.make_synthesiser("cloud") == "voice"


def test_external_speech_is_off_by_default():
    assert settings.VOICE_ALLOW_EXTERNAL is False
    assert settings.VOICE_RECOGNISER == "vosk" and settings.VOICE_SYNTHESISER == "piper"


def test_pitch_markup():
    assert speaker.with_pitch("Hello.", 0, speaker.SVSF_ASYNC) == ("Hello.", speaker.SVSF_ASYNC)
    text, flags = speaker.with_pitch("Tom & <Jerry>", -4, speaker.SVSF_ASYNC)
    assert text == '<pitch absmiddle="-4">Tom &amp; &lt;Jerry&gt;</pitch>'
    assert flags & speaker.SVSF_IS_XML and flags & speaker.SVSF_ASYNC


def test_pitch_preference_is_checked():
    from aurora.config import preferences

    assert preferences._valid("voice_pitch", 5) and not preferences._valid("voice_pitch", 11)
    assert not preferences._valid("voice_pitch", True)
