"""
Research variants of AURORA-T0: one architectural change at a time.

    ArchConfig(model=T0Config(...), norm="layer" | "rms",
                                    ffn="gelu" | "swiglu",
                                    mixer="attention" | "recurrent")

With the defaults (layer norm, GELU feed-forward, attention) a
``VariantModel`` is AURORA-T0 exactly: the same parameter names, the same
initial weights for a seed, and identical logits and gradients (tested).
Each option replaces one part:

- **norm = "rms"**: RMSNorm (no mean subtraction, no shift) in every
  place T0 uses layer norm.
- **ffn = "swiglu"**: the feed-forward becomes silu(x Wg) * (x Wu) Wd
  (no biases), as in LLaMA. Its width is chosen so the parameter count
  matches T0's feed-forward (see ``matched``).
- **mixer = "recurrent"**: attention is replaced by a gated linear
  recurrence, an attention-free token mixer:

        u_t = x_t Wu                      what to remember
        g_t = sigmoid(x_t Wg + bg)        how much of the memory to let out
        a   = sigmoid(lambda)             per-channel decay, learned (0..1)
        h_t = a * h_{t-1} + (1 - a) * u_t
        y_t = (g_t * h_t) Wo + bo

  Each channel is an exponential moving average with its own learned
  memory length (initialised from ~2 to ~32 tokens), so the model mixes
  information over time without comparing every pair of tokens. It costs
  O(T) per sequence instead of O(T^2), and generating a token needs only
  the current state (no growing key/value cache). The same family of
  ideas as RetNet, LRU or minGRU, in its simplest form. Its parameter
  count is matched to T0 by widening the feed-forward.

The backward pass reuses the gradient-checked operations of
aurora_core.tensors.ops; the recurrence has its own reverse scan.
"""

import math
from dataclasses import asdict, dataclass, field, replace

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
    rms_norm,
    rms_norm_backward,
    sigmoid,
    silu,
    silu_backward,
    softmax,
    softmax_backward,
)
from aurora_core.transformer.config import T0Config
from aurora_core.transformer.layers import causal_mask, embed, merge_heads, split_heads

NORMS = ("layer", "rms")
FFNS = ("gelu", "swiglu")
MIXERS = ("attention", "recurrent")


@dataclass(frozen=True)
class ArchConfig:
    model: T0Config = field(default_factory=T0Config)
    norm: str = "layer"
    ffn: str = "gelu"
    mixer: str = "attention"

    def __post_init__(self):
        if self.norm not in NORMS or self.ffn not in FFNS or self.mixer not in MIXERS:
            raise ValueError(f"unknown variant: norm={self.norm!r}, ffn={self.ffn!r}, mixer={self.mixer!r}")

    @property
    def is_t0(self) -> bool:
        return (self.norm, self.ffn, self.mixer) == ("layer", "gelu", "attention")

    def label(self) -> str:
        parts = [part for part, default in ((self.norm, "layer"), (self.ffn, "gelu"), (self.mixer, "attention"))
                 if part != default]
        return "T0" if not parts else "T0 + " + " + ".join(parts)

    def to_dict(self) -> dict:
        return {"model": self.model.to_dict(), "norm": self.norm, "ffn": self.ffn, "mixer": self.mixer}

    @classmethod
    def from_dict(cls, data: dict) -> "ArchConfig":
        return cls(T0Config(**data["model"]), data.get("norm", "layer"), data.get("ffn", "gelu"),
                   data.get("mixer", "attention"))


def matched(config: ArchConfig) -> ArchConfig:
    """Adjusts d_ff so the variant has (nearly) as many parameters as T0 of
    the same size: a fair comparison measures the architecture, not size."""
    m = config.model
    d, f = m.d_model, m.d_ff
    gelu_ffn = 2 * d * f + f + d                         # w_in, b_in, w_out, b_out
    attention = 4 * d * d + 4 * d                        # w_qkv, b_qkv, w_out, b_out
    recurrent = 3 * d * d + 3 * d                        # w_u, w_g, b_g, lam, w_out, b_out
    budget = gelu_ffn + (attention - recurrent if config.mixer == "recurrent" else 0)
    if config.ffn == "swiglu":
        width = max(2, round(budget / (3 * d) / 2) * 2)  # w_gate, w_up, w_down; even width
    else:
        width = max(1, round((budget - d) / (2 * d + 1)))
    return replace(config, model=replace(m, d_ff=width))


# --- Parameters -------------------------------------------------------------------------------

def parameter_shapes(config: ArchConfig) -> dict[str, tuple[int, ...]]:
    m = config.model
    d, f = m.d_model, m.d_ff
    shapes: dict[str, tuple[int, ...]] = {
        "token_embedding": (m.vocab_size, d),
        "position_embedding": (m.context_length, d),
    }

    def norm(name):
        shapes[name + ".gamma"] = (d,)
        if config.norm == "layer":
            shapes[name + ".beta"] = (d,)

    for i in range(m.n_layers):
        p = f"blocks.{i}."
        norm(p + "ln1")
        if config.mixer == "attention":
            shapes.update({p + "attn.w_qkv": (d, 3 * d), p + "attn.b_qkv": (3 * d,),
                           p + "attn.w_out": (d, d), p + "attn.b_out": (d,)})
        else:
            shapes.update({p + "rec.w_u": (d, d), p + "rec.w_g": (d, d), p + "rec.b_g": (d,),
                           p + "rec.lam": (d,), p + "rec.w_out": (d, d), p + "rec.b_out": (d,)})
        norm(p + "ln2")
        if config.ffn == "gelu":
            shapes.update({p + "ffn.w_in": (d, f), p + "ffn.b_in": (f,),
                           p + "ffn.w_out": (f, d), p + "ffn.b_out": (d,)})
        else:
            shapes.update({p + "ffn.w_gate": (d, f), p + "ffn.w_up": (d, f), p + "ffn.w_down": (f, d)})
    norm("ln_f")
    return shapes


def decay_init(d: int) -> np.ndarray:
    """lambda such that sigmoid(lambda) gives memories of about 2 to 32 tokens:
    a = 1 - 1/L for L spaced geometrically between 2 and 32."""
    lengths = np.geomspace(2.0, 32.0, d)
    a = 1.0 - 1.0 / lengths
    return np.log(a / (1.0 - a))


def init_parameters(config: ArchConfig, seed: int = 0) -> dict[str, np.ndarray]:
    """Same scheme as T0 (GPT-2 initialisation), drawn in the same order, so
    the default configuration reproduces T0's initial weights exactly."""
    m = config.model
    rng = np.random.default_rng(seed)
    residual_scale = 1.0 / math.sqrt(2 * m.n_layers)
    params: dict[str, np.ndarray] = {}
    for name, shape in parameter_shapes(config).items():
        leaf = name.rsplit(".", 1)[-1]
        if leaf == "gamma":
            value = np.ones(shape)
        elif leaf == "beta" or leaf.startswith("b_"):
            value = np.zeros(shape)
        elif leaf == "lam":
            value = decay_init(shape[0])
        else:
            std = m.init_std
            if name.endswith(("attn.w_out", "ffn.w_out", "ffn.w_down", "rec.w_out")):
                std *= residual_scale
            value = rng.normal(0.0, std, size=shape)
        params[name] = value.astype(m.np_dtype)
    return params


# --- Forward and backward -------------------------------------------------------------------

def _norm(config, x, params, name):
    eps = config.model.layer_norm_eps
    if config.norm == "layer":
        return layer_norm(x, params[name + ".gamma"], params[name + ".beta"], eps)
    return rms_norm(x, params[name + ".gamma"], eps)


def _norm_backward(config, grad, x, params, name, grads):
    eps = config.model.layer_norm_eps
    if config.norm == "layer":
        dx, grads[name + ".gamma"], grads[name + ".beta"] = layer_norm_backward(grad, x, params[name + ".gamma"], eps)
    else:
        dx, grads[name + ".gamma"] = rms_norm_backward(grad, x, params[name + ".gamma"], eps)
    return dx


def scan(u: np.ndarray, a: np.ndarray) -> np.ndarray:
    """h_t = a * h_{t-1} + (1 - a) * u_t over axis 1 of u (B, T, D), h_{-1} = 0."""
    h = np.empty_like(u)
    state = np.zeros_like(u[:, 0])
    for t in range(u.shape[1]):
        state = a * state + (1.0 - a) * u[:, t]
        h[:, t] = state
    return h


def scan_backward(dh: np.ndarray, u: np.ndarray, h: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Gradients (du, da) of scan(), by a reverse scan."""
    du = np.empty_like(u)
    da = np.zeros_like(a)
    carry = np.zeros_like(u[:, 0])
    for t in reversed(range(u.shape[1])):
        delta = dh[:, t] + carry                          # dL/dh_t, including what flows back from t+1
        previous = h[:, t - 1] if t > 0 else np.zeros_like(carry)
        da += (delta * (previous - u[:, t])).sum(axis=0)
        du[:, t] = delta * (1.0 - a)
        carry = delta * a
    return du, da


def _forward(config, params, tokens):
    m = config.model
    n_heads = m.n_heads
    mask = causal_mask(tokens.shape[1])
    x = embed(tokens, params["token_embedding"], params["position_embedding"])
    caches = []
    for i in range(m.n_layers):
        p = lambda name: params[f"blocks.{i}.{name}"]  # noqa: E731
        c = {"x": x}
        c["ln1"] = _norm(config, x, params, f"blocks.{i}.ln1")
        if config.mixer == "attention":
            qkv = linear(c["ln1"], p("attn.w_qkv"), p("attn.b_qkv"))
            c["q"], c["k"], c["v"] = (split_heads(part, n_heads) for part in np.split(qkv, 3, axis=-1))
            scores = c["q"] @ c["k"].transpose(0, 1, 3, 2) / math.sqrt(m.head_dim)
            c["attn"] = softmax(np.where(mask, scores, -np.inf), axis=-1)
            c["context"] = merge_heads(c["attn"] @ c["v"])
            mixed = linear(c["context"], p("attn.w_out"), p("attn.b_out"))
        else:
            c["u"] = linear(c["ln1"], p("rec.w_u"))
            c["g_pre"] = linear(c["ln1"], p("rec.w_g"), p("rec.b_g"))
            c["g"] = sigmoid(c["g_pre"])
            c["a"] = sigmoid(p("rec.lam"))
            c["h"] = scan(c["u"], c["a"])
            c["gated"] = c["g"] * c["h"]
            mixed = linear(c["gated"], p("rec.w_out"), p("rec.b_out"))
        c["x1"] = x + mixed
        c["ln2"] = _norm(config, c["x1"], params, f"blocks.{i}.ln2")
        if config.ffn == "gelu":
            c["hidden"] = linear(c["ln2"], p("ffn.w_in"), p("ffn.b_in"))
            c["activated"] = gelu(c["hidden"])
            x = c["x1"] + linear(c["activated"], p("ffn.w_out"), p("ffn.b_out"))
        else:
            c["gate"] = linear(c["ln2"], p("ffn.w_gate"))
            c["up"] = linear(c["ln2"], p("ffn.w_up"))
            c["activated"] = silu(c["gate"]) * c["up"]
            x = c["x1"] + linear(c["activated"], p("ffn.w_down"))
        caches.append(c)
    final = {"x": x, "normed": _norm(config, x, params, "ln_f")}
    logits = final["normed"] @ params["token_embedding"].T       # weight tying, as in T0
    return logits, caches, final


@dataclass
class VariantResult:
    logits: np.ndarray


class VariantModel:
    """A research variant with the same interface the trainer uses for T0:
    ``model(tokens)`` gives logits, ``loss_and_gradients`` trains it."""

    def __init__(self, config: ArchConfig | None = None, params: dict[str, np.ndarray] | None = None,
                 seed: int = 0):
        self.arch = config or ArchConfig()
        self.config = self.arch.model                   # the trainer and data loader read model.config
        self.params = params if params is not None else init_parameters(self.arch, seed)
        expected = parameter_shapes(self.arch)
        if set(expected) != set(self.params) or any(self.params[k].shape != v for k, v in expected.items()):
            raise ValueError("parameters don't match the variant's configuration")

    @property
    def num_parameters(self) -> int:
        return int(sum(p.size for p in self.params.values()))

    def _check_tokens(self, tokens) -> np.ndarray:
        tokens = np.asarray(tokens)
        if tokens.ndim not in (1, 2) or tokens.size == 0 or not np.issubdtype(tokens.dtype, np.integer):
            raise ValueError("tokens must be a non-empty integer array of shape (T,) or (B, T)")
        if tokens.shape[-1] > self.config.context_length:
            raise ValueError("sequence longer than the context length")
        if tokens.min() < 0 or tokens.max() >= self.config.vocab_size:
            raise ValueError(f"token ids must be in [0, {self.config.vocab_size})")
        return tokens

    def __call__(self, tokens) -> np.ndarray:
        tokens = self._check_tokens(tokens)
        single = tokens.ndim == 1
        logits = _forward(self.arch, self.params, tokens[None, :] if single else tokens)[0]
        return logits[0] if single else logits

    def loss(self, tokens, targets) -> float:
        return cross_entropy(self(tokens), np.asarray(targets))

    def loss_and_gradients(self, tokens, targets, ignore_index: int | None = None):
        return loss_and_gradients(self, tokens, targets, ignore_index)


def loss_and_gradients(model: VariantModel, tokens, targets, ignore_index: int | None = None):
    config, params = model.arch, model.params
    m = config.model
    tokens = model._check_tokens(tokens)
    targets = np.asarray(targets)
    if tokens.ndim == 1:
        tokens, targets = tokens[None, :], targets[None, :]
    if targets.shape != tokens.shape:
        raise ValueError(f"targets shape {targets.shape} must match tokens shape {tokens.shape}")
    n_heads, scale = m.n_heads, 1.0 / math.sqrt(m.head_dim)
    logits, caches, final = _forward(config, params, tokens)
    loss = cross_entropy(logits, targets, ignore_index)
    grads = {name: np.zeros_like(value) for name, value in params.items()}

    dlogits = cross_entropy_backward(logits, targets, ignore_index).astype(logits.dtype)
    embedding = params["token_embedding"]
    grads["token_embedding"] += dlogits.reshape(-1, dlogits.shape[-1]).T @ final["normed"].reshape(-1, m.d_model)
    dx = _norm_backward(config, dlogits @ embedding, final["x"], params, "ln_f", grads)

    for i in reversed(range(m.n_layers)):
        prefix = f"blocks.{i}."
        p = lambda name: params[prefix + name]  # noqa: E731
        c = caches[i]

        if config.ffn == "gelu":
            dact, grads[prefix + "ffn.w_out"], grads[prefix + "ffn.b_out"] = linear_backward(dx, c["activated"], p("ffn.w_out"))
            dhidden = gelu_backward(dact, c["hidden"])
            dln2, grads[prefix + "ffn.w_in"], grads[prefix + "ffn.b_in"] = linear_backward(dhidden, c["ln2"], p("ffn.w_in"))
        else:
            dact, grads[prefix + "ffn.w_down"], _ = linear_backward(dx, c["activated"], p("ffn.w_down"))
            dgate = silu_backward(dact * c["up"], c["gate"])
            dup = dact * silu(c["gate"])
            dln2_g, grads[prefix + "ffn.w_gate"], _ = linear_backward(dgate, c["ln2"], p("ffn.w_gate"))
            dln2_u, grads[prefix + "ffn.w_up"], _ = linear_backward(dup, c["ln2"], p("ffn.w_up"))
            dln2 = dln2_g + dln2_u
        dx1 = dx + _norm_backward(config, dln2, c["x1"], params, prefix + "ln2", grads)

        if config.mixer == "attention":
            dcontext, grads[prefix + "attn.w_out"], grads[prefix + "attn.b_out"] = linear_backward(dx1, c["context"], p("attn.w_out"))
            dheads = split_heads(dcontext, n_heads)
            dattn = dheads @ c["v"].transpose(0, 1, 3, 2)
            dv = c["attn"].transpose(0, 1, 3, 2) @ dheads
            dscores = softmax_backward(dattn, c["attn"], axis=-1) * scale
            dq = dscores @ c["k"]
            dk = dscores.transpose(0, 1, 3, 2) @ c["q"]
            dqkv = np.concatenate([merge_heads(dq), merge_heads(dk), merge_heads(dv)], axis=-1)
            dln1, grads[prefix + "attn.w_qkv"], grads[prefix + "attn.b_qkv"] = linear_backward(dqkv, c["ln1"], p("attn.w_qkv"))
        else:
            dgated, grads[prefix + "rec.w_out"], grads[prefix + "rec.b_out"] = linear_backward(dx1, c["gated"], p("rec.w_out"))
            dg = dgated * c["h"]
            dh = dgated * c["g"]
            du, da = scan_backward(dh, c["u"], c["h"], c["a"])
            grads[prefix + "rec.lam"] = da * c["a"] * (1.0 - c["a"])
            dg_pre = dg * c["g"] * (1.0 - c["g"])
            dln1_u, grads[prefix + "rec.w_u"], _ = linear_backward(du, c["ln1"], p("rec.w_u"))
            dln1_g, grads[prefix + "rec.w_g"], grads[prefix + "rec.b_g"] = linear_backward(dg_pre, c["ln1"], p("rec.w_g"))
            dln1 = dln1_u + dln1_g
        dx = dx1 + _norm_backward(config, dln1, c["x"], params, prefix + "ln1", grads)

    np.add.at(grads["token_embedding"], tokens.reshape(-1), dx.reshape(-1, m.d_model))
    grads["position_embedding"][: tokens.shape[1]] += dx.sum(axis=0)
    for name, grad in grads.items():
        grads[name] = grad.astype(params[name].dtype, copy=False)
    return loss, grads


def describe(config: ArchConfig) -> dict:
    data = config.to_dict()
    data["label"] = config.label()
    data["parameters"] = int(sum(int(np.prod(s)) for s in parameter_shapes(config).values()))
    return data


__all__ = ["ArchConfig", "VariantModel", "matched", "init_parameters", "parameter_shapes", "loss_and_gradients",
           "scan", "scan_backward", "describe", "asdict"]
