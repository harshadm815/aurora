"""
The building blocks of AURORA-T0, each a pure function of its inputs and
weights (forward pass only; training comes in a later phase).

Shapes: B = batch, T = sequence length, D = d_model, H = heads,
d = head dimension (D / H), V = vocabulary.
"""

import math

import numpy as np

from aurora_core.tensors.ops import gelu, layer_norm, linear, softmax


def embed(
    tokens: np.ndarray, token_embedding: np.ndarray, position_embedding: np.ndarray
) -> np.ndarray:
    """(B, T) token ids -> (B, T, D): token vector plus position vector."""
    seq_len = tokens.shape[-1]
    return token_embedding[tokens] + position_embedding[:seq_len]


def causal_mask(seq_len: int) -> np.ndarray:
    """(T, T) boolean matrix, True where query position i may attend to key
    position j, i.e. j <= i. A token never sees tokens after it."""
    return np.tril(np.ones((seq_len, seq_len), dtype=bool))


def split_heads(x: np.ndarray, n_heads: int) -> np.ndarray:
    """(B, T, D) -> (B, H, T, d)."""
    batch, seq_len, d_model = x.shape
    return x.reshape(batch, seq_len, n_heads, d_model // n_heads).transpose(0, 2, 1, 3)


def merge_heads(x: np.ndarray) -> np.ndarray:
    """(B, H, T, d) -> (B, T, D)."""
    batch, n_heads, seq_len, head_dim = x.shape
    return x.transpose(0, 2, 1, 3).reshape(batch, seq_len, n_heads * head_dim)


def causal_self_attention(
    x: np.ndarray,
    w_qkv: np.ndarray,
    b_qkv: np.ndarray,
    w_out: np.ndarray,
    b_out: np.ndarray,
    n_heads: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Multi-head causal self-attention.

    Returns (output (B, T, D), attention weights (B, H, T, T)).

        Q, K, V = x Wq, x Wk, x Wv        (one combined matrix w_qkv)
        weights = softmax(Q K^T / sqrt(d) with future positions = -inf)
        output  = concat_heads(weights V) Wo
    """
    d_model = x.shape[-1]
    qkv = linear(x, w_qkv, b_qkv)
    q, k, v = (split_heads(part, n_heads) for part in np.split(qkv, 3, axis=-1))
    head_dim = d_model // n_heads

    scores = q @ k.transpose(0, 1, 3, 2) / math.sqrt(head_dim)
    allowed = causal_mask(x.shape[1])
    scores = np.where(allowed, scores, -np.inf)
    weights = softmax(scores, axis=-1)

    context = merge_heads(weights @ v)
    return linear(context, w_out, b_out), weights


def feed_forward(
    x: np.ndarray, w_in: np.ndarray, b_in: np.ndarray, w_out: np.ndarray, b_out: np.ndarray
) -> np.ndarray:
    """Position-wise feed-forward network: linear -> GELU -> linear."""
    return linear(gelu(linear(x, w_in, b_in)), w_out, b_out)


def transformer_block(
    x: np.ndarray, params: dict[str, np.ndarray], prefix: str, n_heads: int, eps: float
) -> tuple[np.ndarray, np.ndarray]:
    """One pre-norm decoder block:

        x = x + attention(layer_norm(x))
        x = x + feed_forward(layer_norm(x))

    Returns (new x, attention weights).
    """
    p = lambda name: params[prefix + name]  # noqa: E731
    normed = layer_norm(x, p("ln1.gamma"), p("ln1.beta"), eps)
    attended, weights = causal_self_attention(
        normed, p("attn.w_qkv"), p("attn.b_qkv"), p("attn.w_out"), p("attn.b_out"), n_heads
    )
    x = x + attended
    normed = layer_norm(x, p("ln2.gamma"), p("ln2.beta"), eps)
    x = x + feed_forward(normed, p("ffn.w_in"), p("ffn.b_in"), p("ffn.w_out"), p("ffn.b_out"))
    return x, weights
