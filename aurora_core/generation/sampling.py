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
