"""
Backpropagation through AURORA-T0.

loss_and_gradients() runs the forward pass while keeping every
intermediate value, computes the cross-entropy loss, then walks the
network backwards applying the chain rule, using the backward functions
from aurora_core.tensors.ops (each of which was gradient-checked on its
own in Phase 1). The result is the gradient of the loss with respect to
every parameter, in a dict with the same names as model.params.

The forward computation here mirrors layers.py / model.py exactly; a
test checks that both give identical logits, and another checks every
gradient against numerical differentiation.
"""

import math

import numpy as np

from aurora_core.tensors.ops import (
    cross_entropy,
    cross_entropy_backward,
    gelu,
    gelu_backward,
    layer_norm,
    layer_norm_backward,
    linear,
    linear_backward,
    softmax,
    softmax_backward,
)
from aurora_core.transformer.layers import causal_mask, embed, merge_heads, split_heads


def _forward(config, params, tokens):
    """Forward pass that keeps what the backward pass needs."""
    eps, n_heads = config.layer_norm_eps, config.n_heads
    head_dim = config.head_dim
    mask = causal_mask(tokens.shape[1])

    x = embed(tokens, params["token_embedding"], params["position_embedding"])
    caches = []
    for i in range(config.n_layers):
        p = lambda name: params[f"blocks.{i}.{name}"]  # noqa: E731
        c = {"x": x}
        c["ln1"] = layer_norm(x, p("ln1.gamma"), p("ln1.beta"), eps)
        qkv = linear(c["ln1"], p("attn.w_qkv"), p("attn.b_qkv"))
        c["q"], c["k"], c["v"] = (split_heads(part, n_heads) for part in np.split(qkv, 3, axis=-1))
        scores = c["q"] @ c["k"].transpose(0, 1, 3, 2) / math.sqrt(head_dim)
        c["attn"] = softmax(np.where(mask, scores, -np.inf), axis=-1)
        c["context"] = merge_heads(c["attn"] @ c["v"])
        c["x1"] = x + linear(c["context"], p("attn.w_out"), p("attn.b_out"))
        c["ln2"] = layer_norm(c["x1"], p("ln2.gamma"), p("ln2.beta"), eps)
        c["hidden"] = linear(c["ln2"], p("ffn.w_in"), p("ffn.b_in"))
        c["activated"] = gelu(c["hidden"])
        x = c["x1"] + linear(c["activated"], p("ffn.w_out"), p("ffn.b_out"))
        caches.append(c)

    final = {"x": x, "normed": layer_norm(x, params["ln_f.gamma"], params["ln_f.beta"], eps)}
    logits = final["normed"] @ params["token_embedding"].T
    return logits, caches, final


def forward_logits(config, params, tokens) -> np.ndarray:
    """Logits from the training forward pass (for checking it against the model)."""
    return _forward(config, params, np.asarray(tokens))[0]


def loss_and_gradients(model, tokens, targets, ignore_index: int | None = None):
    """Cross-entropy loss of `targets` given `tokens` (both (B, T)) and the
    gradient of that loss for every parameter.

    Returns (loss, grads) where grads[name] has the shape of params[name].
    """
    config, params = model.config, model.params
    tokens = model._check_tokens(tokens)
    targets = np.asarray(targets)
    if tokens.ndim == 1:
        tokens, targets = tokens[None, :], targets[None, :]
    if targets.shape != tokens.shape:
        raise ValueError(f"targets shape {targets.shape} must match tokens shape {tokens.shape}")

    eps, n_heads = config.layer_norm_eps, config.n_heads
    scale = 1.0 / math.sqrt(config.head_dim)
    logits, caches, final = _forward(config, params, tokens)
    loss = cross_entropy(logits, targets, ignore_index)

    grads = {name: np.zeros_like(value) for name, value in params.items()}

    # Output layer (tied to the token embedding): logits = normed @ E^T
    dlogits = cross_entropy_backward(logits, targets, ignore_index).astype(logits.dtype)
    embedding = params["token_embedding"]
    grads["token_embedding"] += dlogits.reshape(-1, dlogits.shape[-1]).T @ final["normed"].reshape(-1, config.d_model)
    dnormed = dlogits @ embedding

    dx, grads["ln_f.gamma"], grads["ln_f.beta"] = layer_norm_backward(
        dnormed, final["x"], params["ln_f.gamma"], eps
    )

    for i in reversed(range(config.n_layers)):
        prefix = f"blocks.{i}."
        p = lambda name: params[prefix + name]  # noqa: E731
        c = caches[i]

        # x = x1 + feed_forward(layer_norm(x1))
        dactivated, grads[prefix + "ffn.w_out"], grads[prefix + "ffn.b_out"] = linear_backward(
            dx, c["activated"], p("ffn.w_out")
        )
        dhidden = gelu_backward(dactivated, c["hidden"])
        dln2, grads[prefix + "ffn.w_in"], grads[prefix + "ffn.b_in"] = linear_backward(
            dhidden, c["ln2"], p("ffn.w_in")
        )
        dx1_norm, grads[prefix + "ln2.gamma"], grads[prefix + "ln2.beta"] = layer_norm_backward(
            dln2, c["x1"], p("ln2.gamma"), eps
        )
        dx1 = dx + dx1_norm

        # x1 = x + attention(layer_norm(x))
        dcontext, grads[prefix + "attn.w_out"], grads[prefix + "attn.b_out"] = linear_backward(
            dx1, c["context"], p("attn.w_out")
        )
        dheads = split_heads(dcontext, n_heads)               # d(attn @ v)
        dattn = dheads @ c["v"].transpose(0, 1, 3, 2)
        dv = c["attn"].transpose(0, 1, 3, 2) @ dheads
        # Masked positions have attention 0, so softmax_backward gives them 0.
        dscores = softmax_backward(dattn, c["attn"], axis=-1) * scale
        dq = dscores @ c["k"]
        dk = dscores.transpose(0, 1, 3, 2) @ c["q"]
        dqkv = np.concatenate([merge_heads(dq), merge_heads(dk), merge_heads(dv)], axis=-1)
        dln1, grads[prefix + "attn.w_qkv"], grads[prefix + "attn.b_qkv"] = linear_backward(
            dqkv, c["ln1"], p("attn.w_qkv")
        )
        dx_norm, grads[prefix + "ln1.gamma"], grads[prefix + "ln1.beta"] = layer_norm_backward(
            dln1, c["x"], p("ln1.gamma"), eps
        )
        dx = dx1 + dx_norm

    # Embedding: x = token_embedding[tokens] + position_embedding[:T]
    np.add.at(grads["token_embedding"], tokens.reshape(-1), dx.reshape(-1, config.d_model))
    grads["position_embedding"][: tokens.shape[1]] += dx.sum(axis=0)

    for name, grad in grads.items():
        grads[name] = grad.astype(params[name].dtype, copy=False)
    return loss, grads
