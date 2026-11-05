"""
Helpers for text that comes from outside AURORA (web pages, APIs, files).

Such text is shown to the user and may be given to the Brain as context,
but it is never executed or obeyed. These helpers make it safe to display
and keep it short.
"""

import re

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def clean_text(text: str | None) -> str:
    """Removes control and direction-override characters; tidies whitespace."""
    if not text:
        return ""
    text = _CONTROL.sub("", str(text))
    text = _SPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def one_line(text: str | None) -> str:
    return " ".join(clean_text(text).split())


def shorten(text: str, max_chars: int) -> str:
    """Cuts at the last sentence end before ``max_chars`` (or a word, if none)."""
    text = clean_text(text)
    if len(text) <= max_chars:
        return text

    cut = text[:max_chars]
    ends = list(_SENTENCE_END.finditer(cut))
    if ends and ends[-1].end() > max_chars // 2:
        return cut[: ends[-1].end()]
    return cut.rsplit(" ", 1)[0] + " …"
