"""
Incremental inference with a key/value (KV) cache.

Without a cache, every new token means running the model over the whole
window again: up to context_length positions per token. But in a causal
Transformer the keys and values of earlier positions never change, so
they can be kept. An InferenceSession:

    start(prompt)   runs the prompt once ("prefill"), storing every
                    layer's keys and values; returns the next-token scores
    append(token)   processes only the new token, attending to the
                    stored keys/values; returns the next-token scores

Context window. AURORA-T0 has learned position embeddings for positions
0 .. context_length-1 only, and a stored key already includes its
position, so the cache can't simply slide. When it is full, the session
keeps the most recent `keep` tokens (including the new one) and runs a
fresh prefill on them:
    keep = context_length       identical to the reference sliding window
                                (every token after the first full window
                                costs a full prefill again)
    keep = context_length // 2  (default) a full prefill only every
                                context_length // 2 tokens; in between,
                                the model sees between half and all of
                                the context
"""

import math

import numpy as np

from aurora_core.tensors.ops import gelu, layer_norm, linear, softmax
from aurora_core.transformer import AuroraT0
from aurora_core.transformer.layers import causal_mask, embed, merge_heads, split_heads


class KVCache:
    """Pre-allocated keys and values: (layers, heads, context, head_dim) each."""

    def __init__(self, n_layers: int, n_heads: int, capacity: int, head_dim: int, dtype):
        shape = (n_layers, n_heads, capacity, head_dim)
        self.k = np.zeros(shape, dtype=dtype)
        self.v = np.zeros(shape, dtype=dtype)
        self.capacity = capacity

    @property
    def nbytes(self) -> int:
        return self.k.nbytes + self.v.nbytes


class InferenceSession:
    def __init__(self, model: AuroraT0, keep: int | None = None):
        cfg = model.config
        self.model = model
        self.config = cfg
        self.keep = cfg.context_length // 2 if keep is None else keep
        if not 1 <= self.keep <= cfg.context_length:
            raise ValueError(f"keep must be between 1 and the context length ({cfg.context_length})")
        self.cache = KVCache(cfg.n_layers, cfg.n_heads, cfg.context_length, cfg.head_dim, cfg.np_dtype)
        self.tokens: list[int] = []     # the tokens whose keys/values are in the cache
        self.prefills = 0               # how many times the cache was rebuilt
        self.truncated = 0              # prompt tokens dropped because the prompt was too long

    @property
    def length(self) -> int:
        return len(self.tokens)

    def _check(self, token: int) -> int:
        token = int(token)
        if not 0 <= token < self.config.vocab_size:
            raise ValueError(f"token id {token} is outside the vocabulary")
        return token

    # ------------------------------------------------------------- prefill

    def start(self, prompt_ids) -> np.ndarray:
        """Reset and process a prompt. A prompt longer than the context
        keeps only its last context_length tokens (counted in truncated)."""
        ids = [self._check(t) for t in prompt_ids]
        if not ids:
            raise ValueError("the prompt must contain at least one token")
        context = self.config.context_length
        self.truncated = max(0, len(ids) - context)
        return self._prefill(ids[-context:])

    def _prefill(self, ids: list[int]) -> np.ndarray:
        cfg, params = self.config, self.model.params
        tokens = np.array(ids, dtype=np.int64)[None, :]
        t = tokens.shape[1]
        mask = causal_mask(t)
        x = embed(tokens, params["token_embedding"], params["position_embedding"])
        for i in range(cfg.n_layers):
            p = lambda name: params[f"blocks.{i}.{name}"]  # noqa: E731
            h = layer_norm(x, p("ln1.gamma"), p("ln1.beta"), cfg.layer_norm_eps)
            qkv = linear(h, p("attn.w_qkv"), p("attn.b_qkv"))
            q, k, v = (split_heads(part, cfg.n_heads) for part in np.split(qkv, 3, axis=-1))
            self.cache.k[i, :, :t] = k[0]
            self.cache.v[i, :, :t] = v[0]
            scores = q @ k.transpose(0, 1, 3, 2) / math.sqrt(cfg.head_dim)
            weights = softmax(np.where(mask, scores, -np.inf), axis=-1)
            x = x + linear(merge_heads(weights @ v), p("attn.w_out"), p("attn.b_out"))
            h = layer_norm(x, p("ln2.gamma"), p("ln2.beta"), cfg.layer_norm_eps)
            x = x + linear(gelu(linear(h, p("ffn.w_in"), p("ffn.b_in"))), p("ffn.w_out"), p("ffn.b_out"))
        x = layer_norm(x[0, -1], params["ln_f.gamma"], params["ln_f.beta"], cfg.layer_norm_eps)
        self.tokens = ids
        self.prefills += 1
        return x @ params["token_embedding"].T

    # ---------------------------------------------------------- one token

    def append(self, token: int) -> np.ndarray:
        """Add one token; return the scores for the token after it."""
        token = self._check(token)
        if not self.tokens:
            raise RuntimeError("call start() with a prompt first")
        if len(self.tokens) == self.config.context_length:
            recent = self.tokens[len(self.tokens) - (self.keep - 1):] if self.keep > 1 else []
            return self._prefill(recent + [token])

        cfg, params, cache = self.config, self.model.params, self.cache
        pos = len(self.tokens)
        n_heads, head_dim = cfg.n_heads, cfg.head_dim
        x = params["token_embedding"][token] + params["position_embedding"][pos]
        for i in range(cfg.n_layers):
            p = lambda name: params[f"blocks.{i}.{name}"]  # noqa: E731
            h = layer_norm(x, p("ln1.gamma"), p("ln1.beta"), cfg.layer_norm_eps)
            q, k, v = (part.reshape(n_heads, head_dim) for part in np.split(linear(h, p("attn.w_qkv"), p("attn.b_qkv")), 3))
            cache.k[i, :, pos] = k
            cache.v[i, :, pos] = v
            keys = cache.k[i, :, :pos + 1]                          # (H, pos+1, d)
            values = cache.v[i, :, :pos + 1]
            weights = softmax(np.einsum("hd,htd->ht", q, keys) / math.sqrt(head_dim), axis=-1)
            context = np.einsum("ht,htd->hd", weights, values).reshape(-1)
            x = x + linear(context, p("attn.w_out"), p("attn.b_out"))
            h = layer_norm(x, p("ln2.gamma"), p("ln2.beta"), cfg.layer_norm_eps)
            x = x + linear(gelu(linear(h, p("ffn.w_in"), p("ffn.b_in"))), p("ffn.w_out"), p("ffn.b_out"))
        x = layer_norm(x, params["ln_f.gamma"], params["ln_f.beta"], cfg.layer_norm_eps)
        self.tokens.append(token)
        return x @ params["token_embedding"].T
