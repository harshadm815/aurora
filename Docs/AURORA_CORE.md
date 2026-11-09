# aurora_core: AURORA's own neural network

`aurora_core/` is where AURORA's own language model is built from first
principles, with NumPy as the only dependency. It is separate from the
assistant (`aurora/`): it never imports it, and the assistant doesn't use
it yet. Each part is built and tested on its own, and only connected to
the assistant once it works (blueprint Phase 6 onwards).

Status: **Phases 1–6 are done:**
- tensor mathematics;
- the AURORA-T0 Transformer;
- the tokenizers;
- the training pipeline;
- text generation with experiments;
- the AURORA Core Runtime;
- optimisation (Phase 7), measured against Phase 6;
- research (blueprint Phase 16): architecture variants and reproducible experiments
  (`aurora_core/research/`, see [RESEARCH.md](RESEARCH.md)).

AURORA-T0, trained on *The Adventures of Sherlock Holmes*, beats the
baselines on text it never saw. It writes text from its own weights,
run by AURORA's own runtime with no Ollama involved. See [AURORA_T0_MODEL_CARD.md](AURORA_T0_MODEL_CARD.md).

```
.venv\Scripts\python.exe -m aurora_core              # build T0, run one forward pass
.venv\Scripts\python.exe -m aurora_core.tokenizer    # train BPE on the docs, show the result
.venv\Scripts\python.exe -m aurora_core.training --data Data\pg1661.txt --steps 5000   # train T0
.venv\Scripts\python.exe -m aurora_core.generation --checkpoint Models\aurora_t0_lr1e-2\best.npz --prompt "Sherlock Holmes" --temperature 0.5
.venv\Scripts\python.exe -m aurora_core.generation.evaluate --checkpoint Models\aurora_t0_lr1e-2\best.npz --data Data\pg1661.txt
.venv\Scripts\python.exe -m aurora_core.training.experiments --data Data\pg1661.txt --steps 2000
.venv\Scripts\python.exe -m aurora_core.runtime --model Models\aurora_t0_lr1e-2\best.npz --interactive
.venv\Scripts\python.exe -m aurora_core.runtime --model Models\aurora_t0_lr1e-2\best.npz --benchmark --tokens 300
.venv\Scripts\python.exe -m aurora_core.runtime.benchmark --model Models\aurora_t0_lr1e-2\best.npz
.venv\Scripts\python.exe -m aurora_core.runtime.precision --model Models\aurora_t0_lr1e-2\best.npz --data Data\pg1661.txt
.venv\Scripts\python.exe -m pytest Tests/test_core_tensors.py Tests/test_core_transformer.py Tests/test_core_tokenizer.py Tests/test_core_training.py Tests/test_core_generation.py Tests/test_core_runtime.py Tests/test_core_optimisation.py
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
| `tokenizer/normalize.py` | Text normalisation (NFC + line endings, or none) |
| `tokenizer/special.py` | `<pad>`, `<unk>`, `<bos>`, `<eos>`, ids 0–3 in every tokenizer |
| `tokenizer/base.py` | Shared interface: `encode`, `decode`, `tokens`, id checks, `save` / `Tokenizer.load` (JSON) |
| `tokenizer/char.py` | `CharTokenizer`: one token per character |
| `tokenizer/bpe.py` | `ByteBPETokenizer`: byte-level BPE, training and encoding |
| `transformer/backprop.py` | `loss_and_gradients(model, tokens, targets)`: forward pass with stored intermediates, then the backward pass |
| `training/data.py` | Reading text, removing Gutenberg headers, train/validation/test split, `TokenDataset` (random batches, fixed evaluation windows) |
| `training/baselines.py` | Uniform, unigram and bigram losses |
| `training/optim.py` | `AdamW`, `clip_grad_norm`, `learning_rate` (warm-up + cosine) |
| `training/checkpoint.py` | `save_checkpoint` / `load_checkpoint` (.npz, no pickle) |
| `training/trainer.py` | `TrainConfig`, `train`, `evaluate` |
| `training/__main__.py` | The command line: text file → trained model, metrics and summary |
| `training/experiments.py` | Controlled comparisons of model and training settings (one seed each; see `research/`) |
| `research/arch.py` | One-change variants of T0: RMSNorm, SwiGLU, a gated linear recurrence instead of attention; parameter matching; their backward passes |
| `research/stats.py` | Paired comparisons over seeds with 95 % confidence intervals and the decision rule |
| `research/runner.py` | Specification → runs → analysis → report, with data and code fingerprints, resuming and bit-for-bit verification |
| `research/claims.py`, `research/specs/` | The claims register; experiment specifications |
| `generation/sampling.py` | Next-token distribution: greedy, temperature, top-k, top-p, banned tokens |
| `generation/generate.py` | `generate_text`, `generate_ids`, `stream_ids` (sliding window) |
| `generation/quality.py` | Measures of generated text; bigram sampler for comparison |
| `generation/evaluate.py` | Evaluation of generated text against the baseline and real text |
| `runtime/loader.py` | `load_model`: size limit, SHA-256, checkpoint deserialisation, tokenizer required |
| `runtime/session.py` | `InferenceSession`, `KVCache`: prefill, one-token steps, context-window policy |
| `runtime/runtime.py` | `AuroraRuntime`: load / generate / unload, limits, cancellation, statistics |
| `runtime/__main__.py` | Command line: one prompt, interactive, benchmark |
| `runtime/session.py` (`FastInferenceSession`) | The optimised engine (Phase 7) |
| `generation/sampling.py` (`FastSampler`) | Optimised sampling, same choices as `sample_next` |
| `runtime/quantize.py` | float16 / int8 weight storage |
| `runtime/precision.py` | Compares precisions: size, loss, agreement |
| `runtime/benchmark.py` | Speed benchmarks across engines and model sizes |

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

## Phase 3: tokenizers

A tokenizer turns text into the token ids the model reads, and back.

```python
from aurora_core.tokenizer import ByteBPETokenizer, load_tokenizer

tok = ByteBPETokenizer.train(texts, vocab_size=1024)
ids = tok.encode("Hello Harry!", add_bos=True)   # [2, ...]
tok.decode(ids)                                  # "Hello Harry!"
tok.save("Models/tokenizer.json")
tok = load_tokenizer("Models/tokenizer.json")
```

**Two tokenizers**, as the blueprint suggests:

| | `CharTokenizer` | `ByteBPETokenizer` |
|---|---|---|
| A token is | one character | a byte sequence: a byte, a word piece or a whole word |
| Vocabulary | characters seen in training (+4 special) | 4 special + 256 bytes + learned merges |
| Unknown input | becomes `<unk>`, shown as � (lossy) | impossible: every text is bytes |
| Lossless | only for known characters | always |
| Use | first experiments | AURORA-T0's real tokenizer |

**Byte-level BPE** (as in GPT-2): training counts adjacent token pairs
and repeatedly adds the most frequent one as a new token, until the
vocabulary is full or no pair occurs at least `min_frequency` times.
Text is first split into chunks (a word with its leading space, up to 3
digits, punctuation, whitespace), so tokens never span two words.
Encoding applies the earliest-learned merge again and again, which
reproduces training's segmentation exactly. Training updates pair
counts incrementally and picks the best pair from a heap. Ties go to
the smaller pair of ids, so the same text always gives the same
tokenizer.

Trained on AURORA's own documentation (75,000 characters, vocabulary
1,024), training takes 0.2 s and gives 2.5 characters per token. The
longest learned tokens are whole words such as " conversation" and
" confirmation". Text in scripts the docs don't contain (Hindi, emoji)
still round-trips exactly, as individual bytes.

**Normalisation** (default "nfc"): `\r\n` and `\r` become `\n`, and
Unicode is put in NFC form, so "é" typed as one character and as
"e" + accent give the same tokens. The guarantee is
`decode(encode(text)) == normalize(text)`; with `normalization="none"`
it is `== text`.

**Special tokens** have ids 0–3 in every tokenizer. They are never
produced from text: encoding the string `"<eos>"` gives its characters.
Only `add_bos=True` / `add_eos=True` add them. So text from a user or a
document can't inject control tokens.

**Edge cases**:
- Text containing an unpaired surrogate (invalid Unicode) is rejected
  with a `ValueError`, as it has no UTF-8 form.
- Ids that don't form complete UTF-8 characters (possible once a model
  generates them) decode to � instead of raising an error.
- `decode` rejects ids outside the vocabulary, non-integers and booleans.

**Saving**: JSON with a format name, version, type, normalisation, the
special tokens and the vocabulary (characters, or the merge list from
which the BPE vocabulary is rebuilt). It is written to a temporary file
and then renamed, so an interrupted save can't corrupt it. Loading never
runs code (no pickle). It checks every field: format, version, special
tokens, and merges that refer only to tokens already defined.

## Phase 4: training

**Backpropagation** (`transformer/backprop.py`). The forward pass is
run again, this time keeping every intermediate value. Then the chain
rule is applied backwards through:
- the tied output layer and the final layer norm;
- each block: the feed-forward network, layer norm, the attention output
  projection, the attention weights (softmax), Q, K and V, and the
  residual connections;
- the token and position embeddings.

Each step uses a Phase 1 backward function that was already checked on
its own. The token embedding gets gradient from two places, the input
lookup and the tied output layer, and both are added.

**Data** (`training/data.py`):
- **Splits:** 90 % training, 5 % validation, 5 % test, as contiguous
  blocks cut at paragraph breaks. No sentence is in two parts.
- **No leakage:** the tokenizer is trained on the training part only, so
  nothing about the held-out text leaks into the model.
- **Batches:** each training batch is 32 random windows of 32 tokens.
  The target at each position is the next token.
- **Evaluation:** fixed, non-overlapping windows, so numbers are
  comparable between evaluations.

**Optimisation** (`training/optim.py`):
- **AdamW:** β = (0.9, 0.95). Weight decay 0.1 applies to weight
  matrices and embeddings only, not to biases or layer-norm parameters.
- **Clipping:** the combined gradient norm is clipped to 1.0.
- **Learning rate:** warms up linearly for 100 steps to 3e-3, then
  follows a cosine decay to 3e-4.

**Checkpoints** (`training/checkpoint.py`): one `.npz` file holds:
- the configuration and every parameter;
- the optimizer's moment estimates and step, so training can resume
  exactly;
- the tokenizer and metadata.

Loading uses `allow_pickle=False`, so a checkpoint can't run code. Every
part is validated on load: format, version, configuration, parameter
names and shapes, and that the tokenizer matches the vocabulary.

**Baselines** (`training/baselines.py`). The model must beat these on
held-out text:

| Baseline | Predicts the next token from | Loss on held-out text |
|---|---|---|
| uniform | nothing (all equally likely) | ln V |
| unigram | how common each token is | |
| bigram | the previous token only | |

The bigram baseline is the meaningful one. A model that can't beat it
has learned nothing beyond "which token tends to follow which".

### First trained model: AURORA-T0 on Sherlock Holmes

| | |
|---|---|
| Text | *The Adventures of Sherlock Holmes*, Project Gutenberg #1661 (public domain), 562,203 characters after removing the licence header and footer |
| Split | train 506,133 / validation 29,163 / test 26,907 characters |
| Tokenizer | byte-level BPE, 512 tokens, trained on the training part: 238,215 / 13,830 / 12,459 tokens |
| Model | AURORA-T0 (1 layer, 2 heads, d = 32, context 32, FF 128), 30,176 parameters (the larger vocabulary adds 8,192 over the 256-token default) |
| Training | 5,000 steps × 32 windows × 32 tokens, about 5.1 M tokens, 21 passes over the training text; 214 s on the i3-8130U |

Results on the **test** text (used once, at the end):

| | Loss | Perplexity |
|---|---|---|
| uniform baseline | 6.238 | 512 |
| unigram baseline | 5.263 | 193 |
| bigram baseline | 3.699 | 40.4 |
| **AURORA-T0** (best validation checkpoint, step 4,750) | **3.388** | **29.6** |

Validation loss over training (bigram baseline 3.744):

| Step | 0 | 250 | 500 | 1,000 | 2,000 | 3,000 | 4,000 | 4,750 | 5,000 |
|---|---|---|---|---|---|---|---|---|---|
| Train | | 4.97 | 4.02 | 3.75 | 3.49 | 3.36 | 3.30 | 3.27 | 3.26 |
| Validation | 6.24 | 4.25 | 3.98 | 3.78 | 3.57 | 3.50 | 3.44 | **3.40** | 3.41 |

This meets the Phase 4 completion condition:
- **Loss decreases:** from 6.24 to 3.40.
- **Validation is measured separately**, on text training never sees.
- **Beats the baseline:** on the held-out test text it is better than
  the bigram baseline, by 0.31 nats.

The model uses context beyond the previous token: per token, it is
about 27 % less "surprised" than the bigram model (perplexity 29.6 vs
40.4).

Observations for Phase 5:
- **Mild overfitting:** training loss (3.26) is below validation
  (3.41), and validation had almost stopped improving by the end.
  21 passes over one book is close to the limit; more text helps more
  than more steps.
- **Clipping is always on late in training:** the gradient norm grew
  from about 0.4 to about 2, so the clip at 1.0 was active in most of
  the later steps. Worth comparing other clip values and learning rates
  (one of the blueprint's Phase 5 experiments).

Files (in `Models/aurora_t0/`, ignored by git):
- `best.npz`: the best validation checkpoint (150 KB).
- `last.npz`: the final checkpoint, with optimizer state for resuming.
- `tokenizer.json`.
- `metrics.jsonl`: one line per evaluation.
- `summary.json`.

The training text is in `Data/pg1661.txt`, also ignored by git.

## Phase 5: generation and experiments

**Generation** (`generation/`). The model scores every possible next
token, one is chosen, appended, and the process repeats. Choices:

- **Temperature 0 (greedy):** always the top token. Exact, but loops.
- **Temperature 0.5–1:** sample from the probabilities. Lower is safer,
  higher is more varied.
- **top-k / top-p:** sample only from the k most likely tokens, or the
  smallest set whose probabilities reach p.

Other behaviour:
- **Long texts:** when the text is longer than the 32-token context,
  only the last 32 tokens are fed in (a sliding window), so generation
  can run on indefinitely.
- **Banned tokens:** `<pad>`, `<unk>` and `<bos>` are never generated.
  `<eos>` ends generation.
- **Streaming:** text is released only when a piece forms complete
  characters, never half an emoji.
- **Cost:** each new token is a full forward pass over the window.
  Caching keys and values to avoid that belongs to the runtime (Phase 6).

**Evaluating generated text** (`generation/quality.py`). There's no
single score for good text. Four simple measures are compared across
real held-out text, the bigram baseline and the model:
- real-word rate;
- distinct word pairs;
- repeated 4-grams (loops);
- 6-grams copied from the training text (reciting).

Results are in the [model card](AURORA_T0_MODEL_CARD.md#results). In
short: at temperature 0.5 the model uses real words 86 % of the time
(bigram 62 %, real text 93 %), doesn't loop and copies nothing. Greedy
decoding loops.

**Experiments** (`training/experiments.py`). Each variant changes one
setting of T0 and trains for 2,000 steps on the same data and
tokenizer. Every variant gets 1,024 tokens per step (the batch size is
adjusted for the context length). Results are saved after every variant,
so an interrupted sweep resumes where it stopped. Validation loss, lower
is better:

| Variant | Parameters | Validation loss | vs baseline | Overfit gap |
|---|---|---|---|---|
| T0 baseline (seeds 0, 1, 2) | 30,176 | 3.626 / 3.616 / 3.645 | (mean 3.629) | 0.08 |
| **d_model 64** (FF 256) | 84,928 | **3.186** | **−0.44** | 0.16 |
| **learning rate 1e-2** | 30,176 | **3.438** | **−0.19** | 0.08 |
| 2 layers | 42,880 | 3.582 | −0.05 | 0.07 |
| init std 0.1 | 30,176 | 3.588 | −0.04 | 0.08 |
| no weight decay | 30,176 | 3.608 | −0.02 | 0.08 |
| context 16 | 29,664 | 3.609 | −0.02 | 0.11 |
| 4 heads | 30,176 | 3.619 | −0.01 | 0.08 |
| grad clip 0.5 | 30,176 | 3.620 | −0.01 | 0.07 |
| grad clip 5.0 | 30,176 | 3.622 | −0.01 | 0.08 |
| context 64 | 31,200 | 3.725 | +0.10 | 0.08 |
| learning rate 1e-3 | 30,176 | 3.914 | +0.29 | 0.05 |

The three baseline seeds differ by up to 0.03, so smaller differences
can't be told apart from chance. (Phase 16 repeats comparisons with five
paired seeds and confidence intervals; these single-seed results are guides,
not claims. See [RESEARCH.md](RESEARCH.md).)

What that shows:
- **Width helps most.** d_model 64 is far better, but has 2.8× the
  parameters and twice the overfitting gap. With more text, a wider
  model is the clear next step (T1).
- **The learning rate was too low.** 1e-2 beats 3e-3 by 0.19 at 2,000
  steps. Over 5,000 steps the lead shrank to 0.03 on test (3.354 vs
  3.388), but it held, so 1e-2 is now the default and T0 was retrained
  with it.
- **Probably small real gains:** a second layer and larger initialisation
  (−0.04 to −0.05), just above the noise.
- **No measurable effect:** 4 heads, clipping at 0.5 or 5.0, no weight
  decay, and context 16.
- **Context 64 was worse** in 2,000 steps. This comparison is the least
  fair: each variant's loss is averaged over windows of its own length,
  and a model this small hasn't learned to use distant tokens. It needs
  a longer run to judge.

Raw results with full training curves are in
`Models/experiments/results.json` (not in git).

## Phase 6: the AURORA Core Runtime

The runtime loads a saved AURORA model and generates text using only
`aurora_core` and NumPy: no Ollama, no other model runner, and nothing
from the assistant package.

```python
from aurora_core.runtime import AuroraRuntime
from aurora_core.generation import GenerationConfig

runtime = AuroraRuntime()
runtime.load("Models/aurora_t0_lr1e-2/best.npz")
result = runtime.generate("Sherlock Holmes", GenerationConfig(temperature=0.5),
                          on_text=print)
result.text, result.stats        # stats: tokens, speed, stop reason, ...
runtime.unload()
```

| Blueprint component | Where |
|---|---|
| Model loader, weight deserializer | `runtime/loader.py` (via `training/checkpoint.py`: .npz, no pickle, every part validated) |
| Tokenizer interface | The tokenizer stored in the checkpoint; `runtime.tokenizer` |
| Inference controller | `AuroraRuntime.generate` |
| Forward-pass execution, KV cache | `runtime/session.py` |
| Context manager | `InferenceSession` window policy; long prompts keep their end |
| Sampling, token generation | `generation/sampling.py`, shared with Phase 5 |
| Resource management | `RuntimeLimits`, cancellation, one request at a time, `memory_usage`, `unload` |

**KV cache.** In a causal Transformer, earlier positions' keys and
values never change when new tokens arrive. The session therefore:
1. runs the prompt once and stores every layer's keys and values;
2. for each new token, computes only that token and attends to the
   stored keys and values.

The cache is pre-allocated: layers × heads × context × head size, for
keys and for values (8 KB for T0).

**Context window.** T0 learned position embeddings for positions 0–31
only, and a stored key already includes its position, so the cache
can't simply slide along. When it is full, the session keeps the most
recent `keep` tokens and rebuilds the cache from them:

| `keep` | Behaviour |
|---|---|
| 32 (`--keep 32`) | Exactly the Phase 5 sliding window, but every token after the first 32 needs a rebuild |
| 16 (default) | A rebuild every 16 tokens; between rebuilds the model sees 16–32 tokens of context |

**Resource management:**
- **Model file size limit:** 500 MB by default.
- **Request limits:** at most 2,048 new tokens and 120 seconds per request.
- **Cancellation:** a request can be cancelled from another thread.
- **One request at a time:** a second request is refused at once
  (`RuntimeBusy`) rather than queued.
- **Memory:** `memory_usage()` reports the weights and the cache;
  `unload()` frees them.
- **Failed loads:** a load that fails leaves the previous model in place.
- **Fingerprint:** every loaded model has a SHA-256, so it is always known
  which weights produced a text.

**Baseline measurements** (i3-8130U, AURORA-T0, NumPy, single thread of
Python; this is the documented starting point for Phase 7):

| Measure | Value |
|---|---|
| Load time | 45 ms (150 KB file) |
| Memory | 118 KB weights + 8 KB KV cache |
| Prompt prefill | 0.4 ms |
| One cached step / one full 32-token pass | ≈ 0.25 ms / ≈ 0.68 ms |
| 500 tokens with sampling: reference (Phase 5, no cache) | 1,210 tokens/s |
| 500 tokens: runtime, default window | 1,746 tokens/s (1.44×) |
| 500 tokens: runtime, exact window (`keep` 32) | 1,242 tokens/s (1.03×) |

The cache only saves about a third of the time, because T0 is tiny:
most of the time goes on Python and NumPy call overhead (dozens of small
operations per token) and on sampling, not on arithmetic. That is the
main target for Phase 7 (profiling, fewer and larger operations, faster
sampling). Larger models will benefit from the cache much more.

## Phase 7: optimisation

The rule from the blueprint: optimise only against a correct reference,
and only where measurement says it matters. Every optimisation here has
the Phase 6 code next to it as the reference, and must produce the
**same tokens**.

### 1. Profiling: where the time went

`cProfile` on 2,000 tokens of T0 generation showed the model itself was
not the problem:

| Where | Share of time |
|---|---|
| `TextStream`: re-decoding *all* text so far, for every new token | 73 % |
| The model (cached steps and rebuilds) | 15 % |
| Sampling | 5 % |

Decoding the whole text again each time made the cost grow with the
square of the output length. That's why Phase 6 measured 1,746 tokens/s
at 500 tokens but only about 1,050 at 2,000.

### 2. What was changed

| Change | How | Check |
|---|---|---|
| Incremental text streaming | Each token's bytes go through Python's incremental UTF-8 decoder once; a character is released when all its bytes have arrived | Equal to `decode(all ids)` on 6,000 random sequences with broken UTF-8 and every special token |
| `FastInferenceSession` | Weights unpacked once; layer norm, attention and GELU on the new token with a minimal number of NumPy calls; `matmul` instead of `einsum`; contiguous output matrix | Equal to the reference session at every position: 2e-15 (float64), 1e-6 (float32) |
| `FastSampler` | The exact procedure `rng.choice` uses (cumulative sum, one uniform number), but only over the allowed top-k tokens, with no per-token validation | Identical choices on 9,000 random draws over every setting, including ties |

Each part separately, on T0:
- a cached step went from 343 µs to 92 µs;
- sampling went from 226 µs to 140 µs per token.

### 3. Results against the Phase 6 baseline

The actual Phase 6 code (commit `d6cd2e9`) was checked out next to the
current code and run alternately on the same models. Settings:
temperature 0.5, top-k 40, seed 0, median of 3 runs, two rounds.

| Model | Tokens | Phase 6 | Phase 7 | Speed-up | Same tokens |
|---|---|---|---|---|---|
| AURORA-T0 (30k parameters) | 500 | 1,788–1,871 /s | 5,383–5,743 /s | **3.0×** | yes |
| AURORA-T0 | 2,000 | 1,039–1,055 /s | 5,667–5,735 /s | **5.4×** | yes |
| 0.9M parameters (d 128, 4 layers, context 128) | 500 | 601–605 /s | 1,211–1,245 /s | **2.0×** | yes |
| 0.9M parameters | 2,000 | 420–461 /s | 1,168–1,242 /s | **2.5–2.9×** | yes |

### 4. Scaling, and what limits speed now

`runtime/benchmark.py`, 500 tokens (randomly initialised larger models;
speed doesn't depend on training):

| Model | Parameters | Weights + KV cache | No cache (Phase 5) | Reference engine | Fast engine |
|---|---|---|---|---|---|
| AURORA-T0 | 30,176 | 0.1 + 0.01 MB | 1,067 /s | 2,339 /s | 4,562 /s |
| d 128, 4 layers, context 128 | 875,264 | 3.5 + 0.5 MB | 46 /s | 566 /s | 1,040 /s |
| d 256, 6 layers, context 256 | 4,935,680 | 19.7 + 3.2 MB | 8 /s | 143 /s | 257 /s |

What this shows:
- **The KV cache matters more as models grow.** It's about 2× for T0
  but 32× for the 4.9M model, because without it each token re-runs up
  to 256 positions.
- **The fast engine's gain shrinks with size**, from about 2× to between
  1.2× and 1.8× depending on the run. It mostly removes per-call Python
  overhead, which matters less as matrices get larger.
- **The 4.9M model is limited by memory bandwidth.** Each token reads
  every weight once (about 20 MB in float32) and takes about 4 ms, about
  5 GB/s, close to what this laptop's memory delivers. Python and the
  arithmetic are no longer the limit.
- **Threads:** OpenBLAS with 1 thread is clearly slower for the 4.9M
  model. 2 and 4 threads perform about the same, so the default stays.
- **Noise:** results vary by up to about 20 % between runs on this
  laptop, so all figures are medians and ranges.

The next speed-up for larger models therefore needs fewer bytes per
token: computing directly from float16 or int8 weights. NumPy can't do
that quickly (it has no fast int8 matrix product, and float16 matrix
products aren't hardware-accelerated). It needs the "optimised CPU
kernels that we develop ourselves" the blueprint mentions: compiled
code, the next step beyond NumPy.

### 5. Lower-precision storage and quantisation

`runtime/quantize.py`:
- **What is reduced:** matrices and embeddings. Biases and layer-norm
  parameters stay float32.
- **int8:** symmetric, with one scale per output channel: per column for
  weight matrices, per row for embeddings.
- **On load,** weights are restored to float32, so the saving is on disk
  and in transfer. Memory while running, and speed, are unchanged.

AURORA-T0 (`python -m aurora_core.runtime.precision`):

| Precision | File | Validation loss | Test loss | Greedy choice same as float32 | Largest weight error |
|---|---|---|---|---|---|
| float32 | 149 KB | 3.3624 | 3.3540 | 100 % | 0 |
| float16 | 91 KB | 3.3624 | 3.3540 | 99.9 % | 4.9e-4 |
| int8 | 67 KB | 3.3631 | 3.3544 | 97.2 % | 5.1e-3 |

All three files include the tokenizer (7 KB) and file headers, so the
weights alone shrink by more than these totals suggest.
- **float16** costs nothing measurable.
- **int8** costs 0.0007 in validation loss. About 3 % of greedy choices
  change (383 of 13,824 positions). They are mostly close calls:
  - at those positions, the gap between float32's top two scores has a
    median of 0.024, against 0.47 over all positions;
  - in 95 % of them, int8 picks float32's second choice.

For T0 the file is tiny either way. These measurements matter for larger
models and for a future runtime that computes from the reduced weights
directly.

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

`Tests/test_core_tokenizer.py`:

- **Lossless round trip**, the Phase 3 completion condition: on 15
  hand-picked tricky strings and 300 random ones mixing Latin, Greek,
  Cyrillic, Chinese, Japanese, Korean, emoji, combining accents,
  zero-width joiners, tabs and newlines. Also without normalisation.
- **Encoding matches a reference**: a separate, simple encoder in the
  test applies every merge in learned order. Both must give identical ids.
- **Chunking never drops characters**, and merges never cross word
  boundaries.
- **Training**: the first merge is the most frequent pair; it is
  deterministic; it respects `vocab_size` and `min_frequency`; frequent
  words become single tokens.
- **Special tokens** can't come from text; `add_bos` / `add_eos`;
  `<unk>` handling in the character tokenizer.
- **Saving and loading** reproduce identical encodings. Bad files are
  rejected: wrong format, version, type, normalisation or special
  tokens; malformed or forward-referencing merges; invalid JSON.
- **With AURORA-T0**: BPE token ids go through the model and its output
  ids decode back to text.

`Tests/test_core_training.py`:

- **Backpropagation is correct**: every entry of every parameter's
  gradient in a 2-layer model matches numerical differentiation, with
  and without ignored positions. The training forward pass gives exactly
  the model's logits.
- **Data**: Gutenberg stripping; splits are contiguous and join back to
  the original; targets are inputs shifted by one; evaluation windows
  are fixed.
- **Baselines** on cases with known answers.
- **AdamW** matches a hand calculation, minimises a quadratic, decays
  only matrices, and saves/restores its state. Clipping keeps the
  gradient direction. The schedule warms up and decays as designed.
- **Checkpoints** reproduce the model, optimizer and tokenizer exactly.
  Missing, damaged, foreign, pickled and mismatched files are rejected.
- **Training**:
  - A small model learns a repeating pattern perfectly and beats the
    baseline.
  - Training is reproducible with the same seed.
  - The best checkpoint is kept.
  - Divergence (NaN loss) stops training.
  - The command line runs end to end.

`Tests/test_core_generation.py`:

- **Sampling:**
  - greedy picks the top token;
  - temperature sharpens and flattens as it should;
  - top-k and top-p keep exactly the right tokens;
  - banned tokens get no probability;
  - 20,000 samples match the distribution.
- **Generation is driven by learned weights:** a small model trained on
  a sentence reproduces it, and the same model untrained doesn't. This is
  the Phase 5 completion condition on a case with a known answer.
- **Generation mechanics:**
  - runs past the context length;
  - reproducible with a seed;
  - never writes structural tokens, even when they score highest;
  - stops at `<eos>`;
  - streamed pieces join up to the final text.
- **Quality measures** on texts with known answers, including curly
  apostrophes; the bigram sampler follows its training pairs.
- **Experiments:**
  - variants get equal tokens per step;
  - each changes one setting;
  - results save after every variant, resume after an interruption, and
    start again when settings change.

`Tests/test_core_runtime.py`:

- **The KV cache is exact:**
  - prefill and every cached step match full forward passes over the
    same tokens (to 1e-12 in float64);
  - after the cache fills, for every `keep` from 1 to the context, the
    cache holds exactly the most recent tokens and its scores match;
  - with `keep` = context, results equal the reference sliding window
    token for token;
  - rebuilds happen exactly as often as expected.
- **The runtime matches the reference generator**: identical tokens,
  greedy and sampled, with the same seed.
- **A trained model** reproduces its training sentence through the
  runtime.
- **Loading:**
  - reports parameters, configuration, training step and a correct
    SHA-256;
  - refuses missing, oversized, damaged and tokenizer-less files;
  - a failed load keeps the previous model.
- **Limits and control:**
  - long prompts keep their end and say so;
  - `<eos>` stops generation;
  - structural tokens never appear, even when they are the favourites;
  - cancellation, timeout and token limits work;
  - a second concurrent request is refused;
  - unload frees the model.
- **The command line**: one prompt, benchmark and error handling.
- **Independence**: no runtime module imports Ollama or the assistant.

`Tests/test_core_optimisation.py`:

- **Streaming:**
  - the incremental text stream equals `decode()` on random ids,
    including broken UTF-8 and special tokens;
  - it never shows half a character;
  - it never re-decodes the whole text.
- **The fast session** equals the reference session at every position,
  for several `keep` values, in float64 and float32, and equals full
  forward passes.
- **The fast sampler** makes the same choices as `sample_next` for every
  setting, including ties and a top-k larger than the vocabulary.
- **The fast engine** generates exactly the same tokens, text and cache
  rebuilds as the reference engine: greedy, top-k and top-p.
- **Quantisation:**
  - int8 error is at most half a quantisation step, and zero channels
    are safe;
  - only matrices are reduced;
  - float16 and int8 checkpoints load, run and are smaller;
  - optimizer state is refused at reduced precision;
  - unknown precisions are rejected, and old checkpoints still load.
- **Tools**: the precision comparison and the benchmark (which must
  report identical tokens).

The tests were checked by breaking the code deliberately:
- Phase 7: each of these is caught:
  - a layer-norm variance 1 % off;
  - the attention scale missing;
  - a wrong GELU constant;
  - attention skipping a position;
  - top-k ties dropped;
  - a wrong sampling search;
  - banned tokens allowed;
  - an unknown token not ending a character;
  - an int8 scale off by one level.
- Runtime: each of these is caught:
  - the wrong position embedding in a cached step;
  - a key not stored;
  - values not stored at prefill;
  - attention skipping the newest position;
  - keeping one token too few after overflow;
  - ignoring cancellation, the timeout, the token limit or the banned
    tokens.
- Generation: each of these is caught:
  - top-p keeping too many tokens;
  - ignoring banned tokens;
  - inverted temperature;
  - no sliding window;
  - reading the wrong position's scores;
  - miscounting repeats.
- Backpropagation: each of these makes the gradient check fail:
  - leaving out the attention scaling;
  - dropping a residual connection's gradient;
  - dropping the embedding gradient;
  - swapping Q and K.
- Transformer: removing the causal mask makes 5 tests fail; removing the
  1/√d scaling makes the reference comparison fail.
- Tokenizer: each of these is caught:
  - applying merges in the wrong order;
  - dropping the digit rule from chunking;
  - not decoding special tokens;
  - accepting invalid Unicode;
  - ignoring `min_frequency`.

## Using AURORA's own model in the assistant

The assistant's Brain talks to one model interface, `ModelSwitch`
(`aurora/providers/model_switch.py`), which serves whichever model is chosen
in Settings → Model:

```
Brain ── ModelSwitch ─┬─ "tinyllama", "phi3", …   → OllamaProvider → Ollama
                      └─ "aurora:<folder>"        → CoreRuntimeProvider → AuroraRuntime
                                                    (Models/<folder>/best.npz)
```

- **Choosing it:** AURORA's checkpoints appear in the Settings model list as
  `aurora:aurora_t0`, `aurora:aurora_t0_lr1e-2`, … (every folder in `Models/`
  with a `best.npz`), next to Ollama's models. Or set
  `DEFAULT_MODEL = "aurora:aurora_t0_lr1e-2"` in `aurora/config/settings.py`.
- **No Ollama, no network:** with an `aurora:` model chosen, replies come
  from the Core Runtime in this process; Ollama may be stopped. Tools (the
  calculator, memory, weather…) work as before; their answers don't come
  from the model.
- **Memory:** the checkpoint loads the first time it is needed, and is
  unloaded when AURORA is paused or you switch to another model, so it never
  holds memory alongside an Ollama model.
- **Streaming and stopping:** replies stream into the chat; stopping a reply
  cancels generation.
- **What it can do:** AURORA-T0 continues your message in the style of *The
  Adventures of Sherlock Holmes*. Its context is 32 tokens and it was never
  trained to follow instructions, so only your latest message is given to it
  (not the personality prompt, memories or earlier messages, which it could
  neither fit nor use), and it can't answer questions. The interface is
  ready for a model that can; Ollama stays the default until then.
- Limits: `CORE_MAX_REPLY_TOKENS` (80) and `CORE_TEMPERATURE` (0.8) in
  settings. Bytes the model writes that aren't valid UTF-8 are dropped.

## Not done yet (next phases)

| Missing | Phase |
|---|---|
| Computing directly from float16 / int8 weights: needs compiled CPU kernels (beyond NumPy), the next speed limit for larger models | later |
| More training text and a wider model (T1); longer runs to judge context length | later |
