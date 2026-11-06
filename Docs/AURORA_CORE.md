# aurora_core: AURORA's own neural network

`aurora_core/` is where AURORA's own language model is built from first
principles, with NumPy as the only dependency. It is separate from the
assistant (`aurora/`): it never imports it, and the assistant doesn't use
it yet. Each part is built and tested on its own, and only connected to
the assistant once it works (blueprint Phase 6 onwards).

Status: **Phases 1–5 are done: tensor mathematics, the AURORA-T0
Transformer, the tokenizers, the training pipeline, and text generation
with experiments.** AURORA-T0, trained on *The Adventures of Sherlock
Holmes*, beats the baselines on text it never saw and writes text from
its own weights. See [AURORA_T0_MODEL_CARD.md](AURORA_T0_MODEL_CARD.md).

```
.venv\Scripts\python.exe -m aurora_core              # build T0, run one forward pass
.venv\Scripts\python.exe -m aurora_core.tokenizer    # train BPE on the docs, show the result
.venv\Scripts\python.exe -m aurora_core.training --data Data\pg1661.txt --steps 5000   # train T0
.venv\Scripts\python.exe -m aurora_core.generation --checkpoint Models\aurora_t0_lr1e-2\best.npz --prompt "Sherlock Holmes" --temperature 0.5
.venv\Scripts\python.exe -m aurora_core.generation.evaluate --checkpoint Models\aurora_t0_lr1e-2\best.npz --data Data\pg1661.txt
.venv\Scripts\python.exe -m aurora_core.training.experiments --data Data\pg1661.txt --steps 2000
.venv\Scripts\python.exe -m pytest Tests/test_core_tensors.py Tests/test_core_transformer.py Tests/test_core_tokenizer.py Tests/test_core_training.py Tests/test_core_generation.py
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
| `training/experiments.py` | Controlled comparisons of model and training settings |
| `generation/sampling.py` | Next-token distribution: greedy, temperature, top-k, top-p, banned tokens |
| `generation/generate.py` | `generate_text`, `generate_ids`, `stream_ids` (sliding window) |
| `generation/quality.py` | Measures of generated text; bigram sampler for comparison |
| `generation/evaluate.py` | Evaluation of generated text against the baseline and real text |

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
can't be told apart from chance.

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

The tests were checked by breaking the code deliberately:
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

## Not done yet (next phases)

| Missing | Phase |
|---|---|
| A runtime the assistant can call instead of Ollama (model loader, KV cache, resource management) | 6 |
| More training text and a wider model (T1); longer runs to judge context length | later |
