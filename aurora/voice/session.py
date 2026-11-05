"""
Voice conversations: connects the microphone, the engine and the speaker.

Listening is always started by the user and always visible:

- ``listen``:   one question, then back to typing.
- ``voice on``: hands-free. AURORA waits for "Aurora", answers, and waits
  again until you say "stop listening" or press Ctrl+C.

The microphone is open only during those moments, and closed while
AURORA speaks, so it never hears itself.
"""

import logging
import re
from collections.abc import Callable

from aurora.kernel.permissions import Permission
from aurora.voice.listener import AudioSource, Listener, MicrophoneSource, VoiceUnavailable
from aurora.voice.speaker import Speaker

logger = logging.getLogger(__name__)

COMPONENT = "Voice"
PERMISSIONS = (Permission.MICROPHONE,)

_STOP = re.compile(
    r"^(?:please\s+)?(?:stop\s+listening|voice\s+off|stop|go\s+to\s+sleep|goodbye|good\s+bye|that's\s+all)$",
    re.IGNORECASE,
)


class VoiceSession:

    def __init__(
        self,
        engine,
        listener: Listener,
        speaker: Speaker | None,
        output: Callable[[str], None] = print,
        microphone: Callable[[], AudioSource] = MicrophoneSource,
    ):
        self.engine = engine
        self.listener = listener
        self.speaker = speaker
        self.output = output
        self.microphone = microphone

    def _check_permission(self) -> None:
        missing = self.engine.permissions.missing(COMPONENT, PERMISSIONS)
        if missing:
            raise VoiceUnavailable(
                "I'm not allowed to use the microphone. Grant 'microphone' to "
                "'Voice' in PERMISSION_GRANTS in aurora/config/settings.py."
            )

    def say(self, text: str) -> None:
        if self.speaker is None:
            return
        try:
            self.speaker.speak(text)
        except Exception:
            logger.exception("Speaking failed")

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
