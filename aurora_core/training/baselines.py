"""
Simple baselines a trained model must beat on held-out data.

    uniform  every token equally likely: loss = ln(vocab_size)
    unigram  each token's frequency in the training data (no context)
    bigram   the next token's frequency given only the previous token

All are evaluated on exactly the (input, target) pairs the model is
evaluated on, with add-k smoothing so unseen tokens don't give an
infinite loss. A model that can't beat the bigram baseline hasn't
learned anything beyond "which token tends to follow which".
"""

import math

import numpy as np


def uniform_loss(vocab_size: int) -> float:
    return math.log(vocab_size)


def unigram_loss(train_ids, targets, vocab_size: int, smoothing: float = 1.0) -> float:
    counts = np.bincount(np.asarray(train_ids).reshape(-1), minlength=vocab_size).astype(np.float64)
    probs = (counts + smoothing) / (counts.sum() + smoothing * vocab_size)
    return float(-np.mean(np.log(probs[np.asarray(targets).reshape(-1)])))


def bigram_loss(train_ids, inputs, targets, vocab_size: int, smoothing: float = 0.1) -> float:
    train_ids = np.asarray(train_ids).reshape(-1)
    table = np.zeros((vocab_size, vocab_size), dtype=np.float64)
    np.add.at(table, (train_ids[:-1], train_ids[1:]), 1.0)
    table += smoothing
    table /= table.sum(axis=1, keepdims=True)
    prev = np.asarray(inputs).reshape(-1)
    nxt = np.asarray(targets).reshape(-1)
    return float(-np.mean(np.log(table[prev, nxt])))
