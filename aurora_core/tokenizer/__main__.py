"""
Try AURORA's tokenizers:  .venv\\Scripts\\python.exe -m aurora_core.tokenizer [files...]

Trains a byte-level BPE tokenizer (and a character tokenizer) on the
given text files, by default AURORA's own documentation, and shows how
text is split. Nothing is saved.
"""

import sys
import time
from pathlib import Path

from aurora_core.tokenizer import ByteBPETokenizer, CharTokenizer

PROJECT = Path(__file__).resolve().parents[2]
SAMPLE = "Hello Harry! AURORA's tokenizer handles émojis 🚀 and Hindi नमस्ते too."


def main(argv: list[str]) -> None:
    files = [Path(a) for a in argv] or sorted([PROJECT / "README.md", *(PROJECT / "Docs").glob("*.md")])
    files = [f for f in files if f.is_file()]
    if not files:
        print("No text files found.")
        return
    texts = [f.read_text(encoding="utf-8") for f in files]
    total = sum(len(t) for t in texts)
    print(f"Training on {len(files)} files, {total:,} characters")

    start = time.perf_counter()
    bpe = ByteBPETokenizer.train(texts, vocab_size=1024)
    elapsed = time.perf_counter() - start
    tokens = sum(len(bpe.encode(t)) for t in texts)
    print(f"\nByte-level BPE: {bpe.vocab_size} tokens, trained in {elapsed:.1f} s")
    print(f"  {total / tokens:.2f} characters per token on the training text")
    words = [i for i in range(260, bpe.vocab_size) if bpe.token_bytes(i).strip().isalpha()]
    longest = sorted(words, key=lambda i: len(bpe.token_bytes(i)))[-8:]
    print(f"  longest word tokens: {[bpe.id_to_token(i) for i in longest]}")

    char = CharTokenizer.train(texts)
    print(f"\nCharacter tokenizer: {char.vocab_size} tokens")

    print(f"\nSample: {SAMPLE!r}")
    for name, tok in (("BPE", bpe), ("char", char)):
        ids = tok.encode(SAMPLE)
        back = tok.decode(ids)
        print(f"  {name:4} {len(ids):3} tokens, round trip {'exact' if back == SAMPLE else 'LOSSY: ' + repr(back)}")
    print(f"  BPE pieces: {bpe.tokens(SAMPLE)}")


if __name__ == "__main__":
    main(sys.argv[1:])
