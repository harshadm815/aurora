"""
Voice conversations: the words that end one, and yes / no answers to an offer.

A conversation (Chat's 🗣 Conversation button, or waking AURORA) goes:
AURORA speaks → listens → you speak → AURORA answers aloud → listens
again… It ends when you say one of END_PHRASES, when you say nothing
for a few seconds, or when you press Stop. The microphone is closed
while AURORA speaks (unless barge-in is on) and after the conversation.
"""

import re

END_PHRASES = {
    "that's all", "that is all", "that's it", "that is it", "goodbye", "good bye", "bye", "bye bye",
    "stop listening", "end conversation", "end the conversation", "stop the conversation", "we're done",
    "we are done", "i'm done", "i am done", "nothing else", "that'll be all", "that will be all",
    "go to sleep", "never mind", "nevermind", "no that's all", "thank you that's all", "thanks that's all",
}
YES = {"yes", "yeah", "yep", "yes please", "sure", "ok", "okay", "please", "go ahead", "go on", "please do",
       "sure go ahead", "yes go ahead", "alright", "all right", "of course", "why not", "let's hear it"}
NO = {"no", "nope", "no thanks", "no thank you", "not now", "later", "maybe later", "skip it", "skip",
      "no need", "not today"}

_POLITE = re.compile(r"^(?:aurora\s*,?\s*|hey aurora\s*,?\s*)|[.!?,]+$", re.I)


def normalise(text: str) -> str:
    text = (text or "").lower().strip()
    text = _POLITE.sub("", text).strip()
    return " ".join(text.replace(",", " ").split())


def is_end(text: str) -> bool:
    t = normalise(text)
    return t in END_PHRASES or any(t.endswith(" " + p) for p in ("that's all", "goodbye", "that is all"))


def is_yes(text: str) -> bool:
    return normalise(text) in YES


def is_no(text: str) -> bool:
    return normalise(text) in NO
