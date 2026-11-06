# aurora_core: AURORA's own neural network

`aurora_core/` is where AURORA's own language model is built from first
principles, with NumPy as the only dependency. It is separate from the
assistant (`aurora/`): it never imports it, and the assistant doesn't use
it yet. Each part is built and tested on its own, and only connected to
the assistant once it works (blueprint Phase 6 onwards).

Status: **Phase 1 (tensor mathematics) and Phase 2 (AURORA-T0 forward
pass) are done.** The model has random weights and can't be trained yet,
so its output is meaningless; what has been verified is that it computes
the right thing.

```
.venv\Scripts\python.exe -m aurora_core      # build T0, run one forward pass
.venv\Scripts\python.exe -m pytest Tests/test_core_tensors.py Tests/test_core_transformer.py
```

## Layout

| Module | Contents |
|---|---|
| `tensors/ops.py` | `linear`, `softmax`, `log_softmax`, `cross_entropy`, `gelu`, `layer_norm`, each with a `*_backward` function |
| `tensors/gradcheck.py` | `numerical_gradient` (central differences), `check_gradient`, `relative_error` |
| `transformer/config.py` | `T0Config`: sizes, validated on creation |
| `transformer/parameters.py` | Parameter names and shapes, GPT-2-style random initialisation, counting, validation of loaded weights |
| `transformer/layers.py` | Embedding, causal mask, head split/merge, causal multi-head self-attention, feed-forward, pre-norm decoder block |
| `transformer/model.py` | `AuroraT0`: `forward(tokens)` → logits and attention weights; `loss(tokens, targets)` |

## Phase 1: mathematics

Every operation has a forward and a backward function. The backward
functions aren't used yet (T0 is forward-only); they were written now so
each could be checked against numerical gradients before training
(Phase 4) depends on them.

- **Softmax** subtracts the maximum before `exp()`, so it never
  overflows (`softmax([1000, 1001, 1002])` works), and gives masked
  (`-inf`) entries a probability of exactly 0.
- **Log-softmax and cross-entropy** never take `log(0)`. Cross-entropy
  takes an `ignore_index` for padding positions.
- **GELU** uses the tanh approximation, as GPT-2 does.
- **Layer norm** normalises the last axis and is finite even for
  constant vectors.

**Gradient checking.** For every input of every operation, the analytic
gradient is compared with `(f(x + ε) − f(x − ε)) / 2ε` in float64. An
entry passes when `|analytic − numeric| ≤ 1e-7 + 1e-5 · |numeric|`, the
same rule as `numpy.allclose`. A purely relative test was tried first and
rejected: where the true gradient is ~0 (GELU at x ≈ −4), finite
differences are only accurate to about 1e-9 absolute, which looks like a
large *relative* error even though the formula is exact. The tests also
confirm the checker rejects a gradient that is wrong by 0.1 % in one entry.

## Phase 2: AURORA-T0

A decoder-only (GPT-style) Transformer:

```
token ids (B, T)
  → token embedding + position embedding        (B, T, 32)
  → decoder block × 1:
        x = x + causal_self_attention(layer_norm(x))   2 heads of 16
        x = x + feed_forward(layer_norm(x))            32 → 128 → GELU → 32
  → final layer_norm
  → x @ token_embeddingᵀ (weight tying)        (B, T, 256) logits
```

| Setting | Value |
|---|---|
| Layers | 1 |
| Attention heads | 2 (16 dimensions each) |
| Embedding size | 32 |
| Context length | 32 tokens |
| Feed-forward size | 128 |
| Vocabulary | 256 (one per byte), a placeholder until the tokenizer (Phase 3) |
| Parameters | 21,984 |
| Precision | float32 (float64 available for checking) |

Design choices:

- **Pre-norm blocks** (layer norm before attention / feed-forward, as in
  GPT-2): more stable to train than the original post-norm design.
- **Weight tying**: the output layer reuses the token embedding. That
  saves 8,192 parameters, over a third of the model at this size.
- **Initialisation**: weights ~ Normal(0, 0.02); the two projections
  that add into the residual stream are scaled by 1/√(2 · layers).
  Result: an untrained model's predictions are close to uniform, with a
  starting loss of ≈ ln 256 = 5.545 (measured: 5.53).
- **Inputs are checked**: token ids must be integers inside the
  vocabulary, and sequences can't be empty or longer than the context.
  Loaded weights must have exactly the expected names, shapes and no NaN.

One forward pass on 18 tokens takes a few tens of milliseconds on the
i3-8130U laptop.

## What the tests prove

`Tests/test_core_transformer.py`:

- **Attention is correct**: the vectorised attention matches a separate,
  straightforward loop implementation written in the test.
- **Causality**: changing token *t* never changes the outputs at
  positions before *t*, and does change the output at *t*. Running a
  prefix gives the same outputs as the start of the full sequence.
  Attention weights are probabilities and are exactly 0 for the future.
- **Shapes** for single sequences and batches; batch items don't affect
  each other; full context accepted, longer rejected.
- **Parameters**: exact count, shapes, deterministic seeds, the
  initialisation rules, rejection of bad weights.
- **Sanity**: untrained loss ≈ ln(vocabulary); float32 matches float64;
  larger configurations (3 layers, 3 heads) work.
- **Independence**: `aurora_core` imports only NumPy and the standard
  library (checked by reading the source).

The tests were checked by breaking the code deliberately: removing the
causal mask makes 5 tests fail; removing the 1/√d scaling makes the
reference comparison fail.

## Not done yet (next phases)

| Missing | Phase |
|---|---|
| Tokenizer (byte-level, then BPE), dataset preparation | 3 |
| Backward pass through the whole model, optimiser (Adam), training loop, checkpoints | 4 |
| Text generation (sampling, temperature, KV cache) | 5 |
| Saving/loading models, a runtime the assistant can call instead of Ollama | 6 |
