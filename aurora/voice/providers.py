"""
Speech providers: one interface for every speech recogniser and voice.

The rest of AURORA (the Command Centre, the terminal, voice sessions, the
self-test, later the wake helper and calls) asks for "the recogniser" and
"the voice" here and never names Vosk or Windows SAPI itself, so a
provider can be added or swapped without changing them.

    recogniser = make_recogniser()        # settings.VOICE_RECOGNISER
    voice = make_synthesiser()            # settings.VOICE_SYNTHESISER

Every provider says whether it is **local** (runs on this PC) or not. A
provider that sends audio or text elsewhere is refused unless
settings.VOICE_ALLOW_EXTERNAL is on; it must then also use the network
gate like every other connection. All built-in providers are local.

    Recogniser    listen_once(source, max_seconds, on_level, should_stop, on_partial) -> str
                  wait_for_wake_word(source, should_stop) -> str | None
                  transcribe(audio, rate) -> str
    Synthesiser   speak(text, should_stop) -> bool (False if interrupted)
                  stop(); voices(); set_voice(); set_rate(); set_volume(); set_pitch()
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aurora.config import settings


class ProviderUnavailable(RuntimeError):
    """The chosen provider doesn't exist, isn't installed or isn't allowed."""


@runtime_checkable
class Recogniser(Protocol):
    name: str
    local: bool

    def listen_once(self, source, max_seconds: float | None = None, on_level=None,
                    should_stop=lambda: False, on_partial=None) -> str: ...

    def wait_for_wake_word(self, source, should_stop=lambda: False) -> str | None: ...

    def transcribe(self, audio: bytes, rate: int) -> str: ...


@runtime_checkable
class Synthesiser(Protocol):
    name: str
    local: bool

    def speak(self, text: str, should_stop=lambda: False) -> bool: ...

    def stop(self) -> None: ...


@dataclass(frozen=True)
class Provider:
    name: str              # "vosk", "sapi"
    label: str             # shown in Settings
    local: bool
    factory: Callable      # (**options) -> Recogniser | Synthesiser


def _vosk(**options):
    from aurora.voice.listener import Listener
    return Listener(**options)


def _sapi(**options):
    from aurora.voice.speaker import SapiSpeaker
    return SapiSpeaker(**options)


RECOGNISERS: dict[str, Provider] = {
    "vosk": Provider("vosk", "Vosk (offline, streaming)", True, _vosk),
}
SYNTHESISERS: dict[str, Provider] = {
    "sapi": Provider("sapi", "Windows voices (offline)", True, _sapi),
}


def _choose(kind: str, providers: dict[str, Provider], name: str | None) -> Provider:
    provider = providers.get(name or "")
    if provider is None:
        raise ProviderUnavailable(f"There's no {kind} called '{name}'. Available: {', '.join(providers)}.")
    if not provider.local and not getattr(settings, "VOICE_ALLOW_EXTERNAL", False):
        raise ProviderUnavailable(f"{provider.label} works outside this PC, and external speech processing "
                                  "is switched off (VOICE_ALLOW_EXTERNAL).")
    return provider


def make_recogniser(name: str | None = None, **options):
    """The speech recogniser named in settings (or ``name``)."""
    provider = _choose("speech recogniser", RECOGNISERS, name or settings.VOICE_RECOGNISER)
    return provider.factory(**options)


def make_synthesiser(name: str | None = None, **options):
    """The voice named in settings (or ``name``)."""
    provider = _choose("voice", SYNTHESISERS, name or settings.VOICE_SYNTHESISER)
    return provider.factory(**options)
