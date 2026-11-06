"""
Measuring generated text.

There is no single number for "good text", so several simple, honest
measures are used, always side by side with the same measures for
(a) text sampled from the bigram baseline and (b) real held-out text
from the book. A model is only doing well if it moves from (a) towards (b).

    known_word_rate     share of words that occur in the training text
                        (invented words like "hurthinking" lower it)
    distinct_2          unique word pairs / all word pairs (low = repetitive)
    repeated_4gram_rate share of word 4-grams already used earlier in the
                        same sample (high = loops)
    copied_6gram_rate   share of word 6-grams that appear word for word in
                        the training text (high = reciting, not generating)
"""

import re
from collections.abc import Iterable

import numpy as np

_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")


def words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD.findall(text)]


def ngrams(items: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(items[i:i + n]) for i in range(len(items) - n + 1)]


class Reference:
    """What generated text is compared against: the training text's words
    and 6-grams (built once, reused for every sample)."""

    def __init__(self, training_text: str):
        train_words = words(training_text)
        self.vocabulary = set(train_words)
        self.sixgrams = set(ngrams(train_words, 6))


def text_stats(text: str, reference: Reference) -> dict:
    w = words(text)
    pairs = ngrams(w, 2)
    fours = ngrams(w, 4)
    sixes = ngrams(w, 6)
    seen: set[tuple[str, ...]] = set()
    repeats = 0
    for gram in fours:
        repeats += gram in seen
        seen.add(gram)
    return {
        "words": len(w),
        "known_word_rate": sum(x in reference.vocabulary for x in w) / len(w) if w else 0.0,
        "distinct_2": len(set(pairs)) / len(pairs) if pairs else 0.0,
        "repeated_4gram_rate": repeats / len(fours) if fours else 0.0,
        "copied_6gram_rate": sum(g in reference.sixgrams for g in sixes) / len(sixes) if sixes else 0.0,
    }


def average_stats(texts: Iterable[str], reference: Reference) -> dict:
    stats = [text_stats(t, reference) for t in texts]
    if not stats:
        raise ValueError("no texts to measure")
    return {key: float(np.mean([s[key] for s in stats])) for key in stats[0]}


class BigramSampler:
    """Generates tokens from the bigram baseline (the next token depends
    only on the previous one), for comparison with the model."""

    def __init__(self, train_ids, vocab_size: int, smoothing: float = 0.1):
        from aurora_core.training.baselines import bigram_table

        self.table = bigram_table(train_ids, vocab_size, smoothing)

    def generate(self, prompt_ids, n: int, rng: np.random.Generator, banned=()) -> list[int]:
        out, prev = [], int(prompt_ids[-1])
        for _ in range(n):
            probs = self.table[prev].copy()
            if banned:
                probs[list(banned)] = 0.0
            probs /= probs.sum()
            prev = int(rng.choice(probs.size, p=probs))
            out.append(prev)
        return out
