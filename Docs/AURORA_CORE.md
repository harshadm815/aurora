# aurora_core: AURORA's own neural network

`aurora_core/` is where AURORA's own language model is built from first
principles, with NumPy as the only dependency. It is separate from the
assistant (`aurora/`): it never imports it, and the assistant doesn't use
it yet. Each part is built and tested on its own, and only connected to
the assistant once it works (blueprint Phase 6 onwards).

Status: **Phases 1–3 are done: tensor mathematics, the AURORA-T0
forward pass, and the tokenizers.** The model has random weights and can't be trained yet,
so its output is meaningless; what has been verified is that it computes
the right thing.

```
.venv\Scripts\python.exe -m aurora_core              # build T0, run one forward pass
.venv\Scripts\python.exe -m aurora_core.tokenizer    # train BPE on the docs, show the result
.venv\Scripts\python.exe -m pytest Tests/test_core_tensors.py Tests/test_core_transformer.py Tests/test_core_tokenizer.py
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

The tests were checked by breaking the code deliberately:
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
| Dataset loading, train/validation/test splits, backward pass through the whole model, AdamW, learning-rate schedule, gradient clipping, checkpoints | 4 |
| Text generation (sampling, temperature, KV cache) | 5 |
| Saving/loading models, a runtime the assistant can call instead of Ollama | 6 |
