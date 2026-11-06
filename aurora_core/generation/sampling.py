"""
Choosing the next token from the model's scores (logits).

    temperature 0      greedy: always the most likely token
    temperature < 1    sharper: safer, more repetitive text
    temperature > 1    flatter: more varied, more mistakes
    top_k              only the k most likely tokens may be chosen
    top_p              only the smallest set of tokens whose probabilities
                       add up to p may be chosen ("nucleus" sampling)
"""

import numpy as np

from aurora_core.tensors.ops import softmax


def next_token_distribution(
    logits: np.ndarray,
    temperature: float = 1.0,
    top_k: int | None = None,
    top_p: float | None = None,
    banned: tuple[int, ...] = (),
) -> np.ndarray:
    """Probabilities for the next token after temperature, top-k, top-p
    and banned tokens are applied. With temperature 0, all probability
    goes to the single most likely allowed token."""
    logits = np.array(logits, dtype=np.float64).reshape(-1)
    vocab = logits.size
    if temperature < 0:
        raise ValueError("temperature must be 0 or more")
    if top_k is not None and not 1 <= top_k:
        raise ValueError("top_k must be at least 1")
    if top_p is not None and not 0 < top_p <= 1:
        raise ValueError("top_p must be in (0, 1]")
    if banned:
        logits[list(banned)] = -np.inf
    if not np.any(np.isfinite(logits)):
        raise ValueError("every token is banned")

    if temperature == 0:
        probs = np.zeros(vocab)
        probs[int(np.argmax(logits))] = 1.0
        return probs

    logits = logits / temperature
    if top_k is not None and top_k < vocab:
        threshold = np.partition(logits, -top_k)[-top_k]
        logits[logits < threshold] = -np.inf
    probs = softmax(logits)
    if top_p is not None and top_p < 1:
        order = np.argsort(-probs, kind="stable")
        sorted_probs = probs[order]
        # Keep a token if the tokens before it don't already reach top_p
        # (so the most likely token is always kept).
        keep = np.cumsum(sorted_probs) - sorted_probs < top_p
        mask = np.zeros(vocab, dtype=bool)
        mask[order[keep]] = True
        probs = np.where(mask, probs, 0.0)
        probs /= probs.sum()
    return probs


def sample_next(logits, rng: np.random.Generator, **options) -> int:
    probs = next_token_distribution(logits, **options)
    return int(rng.choice(probs.size, p=probs))


class FastSampler:
    """Chooses the same tokens as sample_next() with the same random
    generator, with less work per token.

    sample_next builds a full-vocabulary distribution and calls
    rng.choice(p=...), which validates p and draws one uniform number u,
    then returns the first index whose cumulative probability exceeds u.
    This class does exactly that draw, but:
      - with top_k, only the k best logits are exponentiated and summed
        (the others have probability 0 and can't change the cumulative sum);
      - banned tokens are excluded with a precomputed mask;
      - nothing is validated per token (the settings are checked once).
    Candidates are kept in vocabulary order, so the cumulative sum and the
    chosen index match rng.choice's (up to the last bit of floating-point
    rounding in the sums).
    """

    def __init__(self, vocab_size: int, temperature: float = 1.0, top_k: int | None = None,
                 top_p: float | None = None, banned: tuple[int, ...] = ()):
        next_token_distribution(np.zeros(vocab_size), temperature, top_k, top_p, ())  # validates settings
        if len(set(banned)) >= vocab_size:
            raise ValueError("every token is banned")
        self.temperature = temperature
        self.top_k = top_k if top_k is not None and top_k < vocab_size - len(set(banned)) else None
        self.top_p = top_p if top_p is not None and top_p < 1 else None
        self._allowed = np.ones(vocab_size, dtype=bool)
        self._allowed[list(banned)] = False
        self._penalty = np.where(self._allowed, 0.0, -np.inf)

    def __call__(self, logits: np.ndarray, rng: np.random.Generator) -> int:
        logits = np.asarray(logits, dtype=np.float64) + self._penalty
        if self.temperature == 0:
            return int(np.argmax(logits))
        logits /= self.temperature
        if self.top_k is not None:
            candidates = np.argpartition(logits, -self.top_k)[-self.top_k:]
            threshold = logits[candidates].min()
            candidates = np.flatnonzero(logits >= threshold)       # ties kept, in vocabulary order
        else:
            candidates = np.flatnonzero(self._allowed)
        values = logits[candidates]
        values -= values.max()
        probs = np.exp(values)
        probs /= probs.sum()
        if self.top_p is not None:
            order = np.argsort(-probs, kind="stable")
            sorted_probs = probs[order]
            keep = np.cumsum(sorted_probs) - sorted_probs < self.top_p
            mask = np.zeros(probs.size, dtype=bool)
            mask[order[keep]] = True
            probs = np.where(mask, probs, 0.0)
            probs /= probs.sum()
        cdf = np.cumsum(probs)
        cdf /= cdf[-1]
        return int(candidates[np.searchsorted(cdf, rng.random(), side="right")])
