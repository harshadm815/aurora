"""
Voice loop-back test: speech synthesis -> file -> speech recognition.

    .venv\\Scripts\\python.exe -m aurora.voice.selftest

For each test sentence and each installed Windows voice (and the Piper
voice, when it's installed), AURORA speaks
the sentence into a WAV file (nothing is played, no microphone is used),
then recognises it with Vosk, and measures:

    word error rate    how many words were wrong (substituted, missing or extra)
    command accuracy   whether the transcript leads to the AURORA action the
                       sentence is meant to (weather, calculator, numbers, …),
                       which is what matters in use

It checks the whole chain, recognition and synthesis together, with
nothing but files. A synthetic voice is clearer than a real one in a real
room, so these numbers are an upper bound; the real test is speaking to
AURORA yourself.
"""

import argparse
import json
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from aurora.voice.listener import apply_corrections, numbers_to_digits

# (sentence, the AURORA action it must lead to)
SENTENCES = [
    ("what is the weather in Delhi tomorrow", "weather"),
    ("what is twelve times seven", "calculator"),
    ("remember that my exam is on Friday", "remember"),
    ("set a timer for ten minutes", "reminder"),
    ("what do my notes say about dark matter", "knowledge_base"),
    ("turn on the desk light", "devices"),
    ("who wrote Hamlet", "conversation"),
    ("what is the average of four eight and fifteen", "numbers"),
    ("what did we do today", "episodes"),
    ("stop listening", "conversation"),        # the voice session itself catches this phrase
]

_WORD = re.compile(r"[a-z0-9']+")


def normalize(text: str) -> list[str]:
    return _WORD.findall(numbers_to_digits(apply_corrections(text)).lower())


def word_errors(reference: list[str], heard: list[str]) -> int:
    """Levenshtein distance over words."""
    previous = list(range(len(heard) + 1))
    for i, ref in enumerate(reference, start=1):
        current = [i] + [0] * len(heard)
        for j, word in enumerate(heard, start=1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref != word))
        previous = current
    return previous[-1]


@dataclass
class Trial:
    voice: str
    sentence: str
    heard: str
    errors: int
    words: int
    expected: str
    got: str
    correct_command: bool
    seconds: float


PIPER_PREFIX = "Piper "


def piper_voices() -> list[str]:
    """The Piper voice in use, if piper-tts and its voice file are installed (else nothing)."""
    try:
        import piper  # noqa: F401
    except ImportError:
        return []
    from aurora.config import settings
    if not (settings.PIPER_VOICE_DIR / f"{settings.PIPER_VOICE}.onnx").is_file():
        return []
    return [PIPER_PREFIX + settings.PIPER_VOICE]


def run(sentences=SENTENCES, voices: list[str] | None = None, listener=None, speaker_factory=None,
        on_trial=None) -> list[Trial]:
    from aurora.routing.router import IntentRouter
    from aurora.voice.listener import Listener, WavSource
    from aurora.voice.speaker import SapiSpeaker

    listener = listener or Listener()
    if speaker_factory is None:
        def speaker_factory(voice):
            if voice.startswith(PIPER_PREFIX):
                from aurora.voice.piper_voice import PiperSpeaker
                return PiperSpeaker(voice=voice[len(PIPER_PREFIX):])
            return SapiSpeaker(voice=voice)
    router = IntentRouter()
    voices = voices or SapiSpeaker().voices() + piper_voices()
    trials = []
    with tempfile.TemporaryDirectory() as folder:
        for voice in voices:
            speaker = speaker_factory(voice)
            for number, (sentence, expected_intent) in enumerate(sentences):
                path = Path(folder) / f"{number}.wav"
                started = time.perf_counter()
                speaker.save_to_file(sentence, path)
                heard = listener.listen_once(WavSource(path))
                expected, got = normalize(sentence), normalize(heard)
                got_intent = router.route(" ".join(got)).intent.value
                trial = Trial(voice, sentence, heard, word_errors(expected, got), len(expected), expected_intent,
                              got_intent, got_intent == expected_intent, round(time.perf_counter() - started, 2))
                trials.append(trial)
                if on_trial:
                    on_trial(trial)
    return trials


def summary(trials: list[Trial]) -> dict:
    words = sum(t.words for t in trials) or 1
    return {
        "trials": len(trials),
        "word_error_rate": round(sum(t.errors for t in trials) / words, 3),
        "command_accuracy": round(sum(t.correct_command for t in trials) / (len(trials) or 1), 3),
        "seconds_per_sentence": round(sum(t.seconds for t in trials) / (len(trials) or 1), 2),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aurora.voice.selftest")
    parser.add_argument("--out", help="save the results as JSON")
    args = parser.parse_args(argv)
    print("Loading the speech model…", flush=True)
    def show(t):
        mark = "ok " if t.correct_command else "!! "
        print(f"{mark}{t.voice.split(' - ')[0]:24} {t.sentence:48} -> {t.heard}  [{t.got}]", flush=True)

    trials = run(on_trial=show)
    result = summary(trials)
    print(f"\nWord error rate {result['word_error_rate']:.1%} · right command {result['command_accuracy']:.0%} "
          f"of {result['trials']} · {result['seconds_per_sentence']} s per sentence")
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": result, "trials": [asdict(t) for t in trials]}, indent=2),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
