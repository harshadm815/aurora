"""
Voice conversations: connects the microphone, the engine and the speaker.

Listening is always started by the user and always visible:

- ``listen``:   one question, then back to typing.
- ``voice on``: hands-free. AURORA waits for "Aurora", answers, and waits
  again until you say "stop listening" or press Ctrl+C.

The microphone is open only during those moments. While AURORA speaks it
is closed, so AURORA never hears itself, unless barge-in is switched on
(settings.VOICE_BARGE_IN, for headphones): then it listens with a
stricter detector and stops talking as soon as you start.

Every spoken reply can be interrupted: ``interrupt()`` (a key press in the
terminal, the Stop button in the Command Centre) is checked between words.
"""

import logging
import re
import threading
from collections.abc import Callable

from aurora.config import settings
from aurora.kernel.permissions import Permission
from aurora.voice.listener import AudioSource, Listener, MicrophoneSource, VoiceUnavailable
from aurora.voice.speaker import Speaker
from aurora.voice.vad import VadSettings, VoiceActivityDetector

logger = logging.getLogger(__name__)

COMPONENT = "Voice"
PERMISSIONS = (Permission.MICROPHONE,)

_STOP = re.compile(
    r"^(?:please\s+)?(?:stop\s+listening|voice\s+off|stop|go\s+to\s+sleep|goodbye|good\s+bye|that's\s+all)$",
    re.IGNORECASE,
)

# Barge-in must not trigger on breathing or a chair creaking: louder and longer than normal speech start.
BARGE_IN_VAD = VadSettings(ratio=4.0, minimum_rms=800.0, start_frames=6, end_silence=1.0, no_speech_timeout=10_000)


class BargeIn:
    """While AURORA speaks: listens on the microphone and sets ``heard``
    when the user starts talking. Use as a context manager."""

    def __init__(self, microphone: Callable[[], AudioSource]):
        self.microphone = microphone
        self.heard = threading.Event()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: Exception | None = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, name="aurora-barge-in", daemon=True)
        self._thread.start()
        return self

    def _run(self):
        try:
            with self.microphone() as source:
                detector = VoiceActivityDetector(source.rate, BARGE_IN_VAD)
                for chunk in source.chunks():
                    if self._done.is_set():
                        return
                    detector.feed(chunk)
                    if detector.started:
                        logger.info("Barge-in: the user started talking")
                        self.heard.set()
                        return
        except Exception as error:   # no microphone: speaking simply can't be interrupted by voice
            self.error = error

    def __exit__(self, *exc):
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


class VoiceSession:

    def __init__(
        self,
        engine,
        listener: Listener,
        speaker: Speaker | None,
        output: Callable[[str], None] = print,
        microphone: Callable[[], AudioSource] = MicrophoneSource,
        interrupt: Callable[[], bool] = lambda: False,
        barge_in: bool | None = None,
    ):
        self.engine = engine
        self.listener = listener
        self.speaker = speaker
        self.output = output
        self.microphone = microphone
        self.interrupt = interrupt
        self.barge_in = settings.VOICE_BARGE_IN if barge_in is None else barge_in

    def _check_permission(self) -> None:
        missing = self.engine.permissions.missing(COMPONENT, PERMISSIONS)
        if missing:
            raise VoiceUnavailable(
                "I'm not allowed to use the microphone. Grant 'microphone' to "
                "'Voice' in PERMISSION_GRANTS in aurora/config/settings.py."
            )

    def say(self, text: str) -> bool:
        """Speaks ``text``. Returns False if it was interrupted (key, button or voice)."""
        if self.speaker is None:
            return True
        try:
            if self.barge_in and not self.engine.permissions.missing(COMPONENT, PERMISSIONS):
                with BargeIn(self.microphone) as monitor:
                    finished = self.speaker.speak(text, lambda: self.interrupt() or monitor.heard.is_set())
            else:
                finished = self.speaker.speak(text, self.interrupt)
        except Exception:
            logger.exception("Speaking failed")
            return True
        if not finished:
            self.output("(Stopped speaking.)")
        return finished

    def _respond(self, heard: str) -> None:
        self.output(f"You (voice): {heard}")
        reply = self.engine.process_message(heard)
        self.output(f"\nAURORA: {reply}\n")
        self.say(reply)

    # --- One question -----------------------------------------------------

    def listen_once(self) -> str:
        """Records one utterance, answers it, and returns what was heard."""
        self._check_permission()

        self.output("🎙  Listening… (speak now)")
        with self.microphone() as source:
            heard = self.listener.listen_once(source)
        self.output("🎙  Microphone off.")

        if not heard:
            self.output("AURORA: I didn't hear anything.\n")
            return ""

        self._respond(heard)
        return heard

    # --- Hands-free -------------------------------------------------------

    def run_hands_free(self, should_stop: Callable[[], bool] = lambda: False) -> None:
        """Wake-word loop. Ends on a stop phrase, Ctrl+C or ``should_stop()``."""
        self._check_permission()
        self.output("🎙  Hands-free on. Say \"Aurora\" to talk to me, \"stop listening\" to end, or press Ctrl+C.")

        try:
            while not should_stop():
                with self.microphone() as source:
                    request = self.listener.wait_for_wake_word(source, should_stop)

                if request is None:
                    break

                if not request:
                    self.say("Yes?")
                    with self.microphone() as source:
                        request = self.listener.listen_once(source)
                    if not request:
                        continue

                if _STOP.match(request.strip()):
                    self.say("Okay, I've stopped listening.")
                    break

                self._respond(request)
        except KeyboardInterrupt:
            pass
        finally:
            self.output("🎙  Hands-free off. Microphone closed.\n")
