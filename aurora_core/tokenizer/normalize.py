"""
Text normalisation, applied before encoding.

"nfc" (the default):
  - line endings: "\\r\\n" and "\\r" become "\\n";
  - Unicode NFC: characters that can be written in more than one way
    (e.g. "e" + combining accent vs the single character "é") are
    stored in one canonical form, so identical-looking text gets
    identical tokens.
"none": the text is used exactly as given.

Both forms are canonically equivalent to the input: nothing a reader
would see is lost. The guarantee is decode(encode(text)) == normalize(text).
"""

import unicodedata

NORMALIZATIONS = ("nfc", "none")


def normalize(text: str, form: str = "nfc") -> str:
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    if form == "none":
        return text
    if form != "nfc":
        raise ValueError(f"normalization must be one of {NORMALIZATIONS}, got {form!r}")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)
