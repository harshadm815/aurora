"""
The Calling Agent: a spoken conversation with AURORA over the phone link.

The phone opens ``GET /api/call`` (signed like every request) and the
server answers ``101 Switching Protocols``; from then on both sides send
frames on the same connection (USB, or HTTPS on the home Wi-Fi):

    1 byte type + 4 bytes length (big-endian) + payload
    A   phone → PC: microphone audio, 16 kHz mono 16-bit (sent continuously)
    P   PC → phone: AURORA's voice, 16-bit mono at the rate last announced
    J   either way: JSON. PC → phone: {"state": "listening" | "thinking" |
        "speaking"}, {"heard"}, {"reply"}, {"rate"}, {"flush": true} (stop
        playing: you interrupted), {"end": reason}. Phone → PC: {"hangup": true}.

Turn-taking: AURORA speaks, then listens. The voice activity detector
notices when you stop (1 s of quiet) and Vosk recognises what you said;
it goes through the same chat as everything else (tools, permissions,
confirmations), and the answer is spoken sentence by sentence at real-time
pace. If you start talking while AURORA speaks (the phone cancels its own
echo), AURORA stops at once and listens. "Goodbye" / "that's all" ends the
call, as do 20 s of silence, the phone hanging up, or 30 minutes.

Nothing is recorded or logged: audio lives only in memory for the moment
it is recognised; the access log records that a call happened and how long.
"""

import json
import logging
import queue
import threading
import time

from aurora.mobile.speech import SpeechError
from aurora.voice.conversation import is_end
from aurora.voice.vad import VadSettings, VoiceActivityDetector

logger = logging.getLogger(__name__)

RATE = 16000
MAX_FRAME = 1024 * 1024
NO_SPEECH_SECONDS = 20.0
NO_AUDIO_SECONDS = 10.0            # the phone stopped sending: the line is dead
MAX_CALL_SECONDS = 30 * 60
CHUNK_SECONDS = 0.1                # AURORA's voice is sent in pieces this long
AHEAD_SECONDS = 0.4                # ...staying at most this far ahead of real time
GREETING = "Hi, it's AURORA. How can I help?"
GOODBYE = "Okay. Bye for now."


class CallEnded(Exception):
    pass


def write_frame(out, kind: bytes, payload: bytes) -> None:
    out.write(kind + len(payload).to_bytes(4, "big") + payload)
    out.flush()


def _exactly(source, count: int) -> bytes:
    data = b""
    while len(data) < count:
        piece = source.read(count - len(data))
        if not piece:
            raise EOFError
        data += piece
    return data


def read_frame(source) -> tuple[bytes, bytes]:
    header = _exactly(source, 5)
    length = int.from_bytes(header[1:], "big")
    if length > MAX_FRAME:
        raise ValueError("frame too large")
    return header[:1], _exactly(source, length) if length else b""


def greeting_for(reason: str, text: str) -> str:
    if reason == "reminder" and text.strip():
        return f"Hi, it's AURORA with your reminder: {text.strip()}. Anything else?"
    if reason == "pc":
        return "Hi, it's AURORA. You asked me to call. How can I help?"
    return GREETING


class CallSession:
    """One call, run on the request's own thread by ``run()`` (returns when the call ends)."""

    def __init__(self, source, out, speech, chat, pending=lambda: False, may_approve: bool = False,
                 reason: str = "", text: str = "", clock=time.monotonic, realtime: bool = True,
                 barge_settings: VadSettings | None = None):
        self.source, self.out = source, out
        self.speech = speech
        self.chat = chat
        self.pending = pending
        self.may_approve = may_approve
        self.greeting = greeting_for(reason, text)
        self.clock = clock
        self.realtime = realtime
        self.barge_settings = barge_settings or VadSettings(ratio=4.0, minimum_rms=900, start_frames=8,
                                                            end_silence=0.6, no_speech_timeout=1e9,
                                                            calibration=0.3)
        self.audio: queue.Queue = queue.Queue()
        self.hung_up = threading.Event()
        self.carry: list[bytes] = []            # what you said while interrupting AURORA
        self.turns = 0
        self.ended = ""
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------ plumbing

    def send(self, **data) -> None:
        with self._write_lock:
            write_frame(self.out, b"J", json.dumps(data).encode("utf-8"))

    def _play(self, pcm: bytes) -> None:
        with self._write_lock:
            write_frame(self.out, b"P", pcm)

    def _read(self) -> None:
        try:
            while not self.hung_up.is_set():
                kind, payload = read_frame(self.source)
                if kind == b"A":
                    self.audio.put(payload)
                elif kind == b"J":
                    if json.loads(payload or b"{}").get("hangup"):
                        break
        except (EOFError, OSError, ValueError):
            pass
        finally:
            self.hung_up.set()
            self.audio.put(None)

    def _next_audio(self, timeout: float = 0.5) -> bytes | None:
        """The next piece of microphone audio; b"" if none arrived in time; raises CallEnded."""
        if self.hung_up.is_set() and self.audio.empty():
            raise CallEnded("hangup")
        try:
            chunk = self.audio.get(timeout=timeout)
        except queue.Empty:
            return b""
        if chunk is None:
            raise CallEnded("hangup")
        return chunk

    def _drain(self) -> None:
        """Drops audio that arrived while AURORA was thinking or speaking (it's mostly echo)."""
        while True:
            try:
                if self.audio.get_nowait() is None:
                    self.audio.put(None)
                    return
            except queue.Empty:
                return

    # ------------------------------------------------------------ the call

    def run(self) -> str:
        reader = threading.Thread(target=self._read, name="aurora-call-reader", daemon=True)
        reader.start()
        started = self.clock()
        self.speech.in_call(True)
        # Load the recogniser while the greeting plays (about 8 s the first time), not after it.
        threading.Thread(target=self._warm_up, name="aurora-call-warmup", daemon=True).start()
        try:
            self.say(self.greeting)
            while True:
                if self.clock() - started > MAX_CALL_SECONDS:
                    self.say("We've been talking for half an hour, so I'll hang up now. Call again any time.")
                    raise CallEnded("too long")
                heard = self.listen()
                if heard is None:
                    self.say("I didn't hear anything, so I'll hang up. Bye for now.")
                    raise CallEnded("silence")
                if not heard:
                    continue
                self.turns += 1
                self.send(heard=heard)
                if is_end(heard):
                    self.say(GOODBYE)
                    raise CallEnded("goodbye")
                self.say(self.answer(heard))
        except CallEnded as end:
            self.ended = str(end)
        except SpeechError as error:
            self.ended = "speech unavailable"
            try:
                self.send(error=str(error))
            except OSError:
                pass
        except OSError:
            self.ended = "connection lost"
        finally:
            self.speech.in_call(False)
            self.hung_up.set()
            try:
                self.send(end=self.ended or "ended")
            except OSError:
                pass
        logger.info("Mobile: call ended (%s) after %d turn(s), %.0f s", self.ended, self.turns,
                    self.clock() - started)
        return self.ended

    def _warm_up(self) -> None:
        try:
            self.speech.prepare()
        except Exception:
            pass                                  # listen() reports it properly

    def answer(self, heard: str) -> str:
        self.send(state="thinking")
        if self.pending() and not self.may_approve:
            return "I'm waiting for an answer to an action, and this phone isn't allowed to approve actions. " \
                   "Please answer it on the PC."
        try:
            reply = self.chat(heard)
        except Exception:
            logger.exception("Mobile: call chat failed")
            return "Sorry, something went wrong on the PC."
        finally:
            self._drain()
        return reply.get("reply") or "Sorry, I have no answer to that."

    def listen(self) -> str | None:
        """What you said ("" for a noise), or None after NO_SPEECH_SECONDS of silence."""
        self.send(state="listening")
        recogniser = self.speech.recogniser(RATE)
        detector = VoiceActivityDetector(RATE, VadSettings(end_silence=1.0, no_speech_timeout=NO_SPEECH_SECONDS))
        pending, self.carry = self.carry, []
        last_audio = self.clock()
        shown = ""
        while True:
            chunk = pending.pop(0) if pending else self._next_audio()
            if chunk:
                last_audio = self.clock()
            elif self.clock() - last_audio > NO_AUDIO_SECONDS:
                raise CallEnded("no audio from the phone")
            else:
                continue
            detector.feed(chunk)
            if recogniser.AcceptWaveform(chunk):
                text = json.loads(recogniser.Result()).get("text", "").strip()
                if text and detector.started:
                    return self._clean(text)
            elif detector.started:
                partial = json.loads(recogniser.PartialResult()).get("partial", "").strip()
                if partial and partial != shown:
                    shown = partial
                    self.send(partial=partial)
            if detector.ended:
                return self._clean(json.loads(recogniser.FinalResult()).get("text", "").strip())
            if detector.gave_up:
                return None

    @staticmethod
    def _clean(text: str) -> str:
        from aurora.voice.listener import apply_corrections, numbers_to_digits
        return numbers_to_digits(apply_corrections(text)) if text else ""

    def say(self, text: str) -> bool:
        """Speaks ``text`` to the phone. False if you interrupted (what you said is kept for listen())."""
        self.send(state="speaking", reply=text)
        barge = VoiceActivityDetector(RATE, self.barge_settings)
        heard: list[bytes] = []
        began = self.clock()
        sent_seconds = 0.0
        rate = None
        for sentence_rate, pcm in self.speech.sentences(text):
            if sentence_rate != rate:
                rate = sentence_rate
                self.send(rate=rate)
            step = int(rate * CHUNK_SECONDS) * 2
            for start in range(0, len(pcm), step):
                if self.hung_up.is_set():
                    raise CallEnded("hangup")
                self._play(pcm[start:start + step])
                sent_seconds += len(pcm[start:start + step]) / 2 / rate
                # Listen for you interrupting while staying close to real time.
                while True:
                    try:
                        chunk = self.audio.get_nowait()
                    except queue.Empty:
                        chunk = b""
                    if chunk is None:
                        raise CallEnded("hangup")
                    if chunk:
                        heard.append(chunk)
                        heard = heard[-15:]                       # about the last 1.5 s
                        barge.feed(chunk)
                        if barge.started:
                            self.send(flush=True)
                            self.carry = heard
                            return False
                    if not self.realtime or sent_seconds - (self.clock() - began) < AHEAD_SECONDS:
                        break
                    time.sleep(0.02)
        if self.realtime:                                      # let the phone finish playing
            while self.clock() - began < sent_seconds and not self.hung_up.is_set():
                time.sleep(0.05)
        self._drain()
        return True
