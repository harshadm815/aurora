# AURORA-T0 model card

AURORA-T0 is the first language model built and trained entirely by
AURORA's own code (`aurora_core/`, NumPy only). It started from random
weights and learned only from the training text below.

## Summary

| | |
|---|---|
| Type | Decoder-only Transformer (GPT-style), pre-norm, tied input/output embeddings |
| Size | 1 layer, 2 heads, d_model 32, feed-forward 128, context 32 tokens |
| Parameters | 30,176 |
| Tokenizer | Byte-level BPE, 512 tokens, trained on the training text |
| Training text | *The Adventures of Sherlock Holmes* by Arthur Conan Doyle, Project Gutenberg #1661 (public domain), licence header and footer removed: 506,133 characters (90 %) |
| Held out | Validation 29,163 characters (5 %), test 26,907 characters (5 %), contiguous, never trained on |
| Training | 5,000 steps × 32 windows × 32 tokens (5.1 M tokens, about 21 passes over the text). AdamW (β 0.9/0.95, weight decay 0.1 on matrices). Peak learning rate 1e-2 with 100-step warm-up and cosine decay to 1e-3. Gradient clipping at 1.0. Seed 0. |
| Hardware / time | Intel i3-8130U, 4 GB RAM, CPU only: 262 s |
| Files | `Models/aurora_t0_lr1e-2/best.npz` (weights, configuration and tokenizer; 150 KB), `metrics.jsonl`, `summary.json`, `generation_eval.json` (not in git) |

The first T0, from Phase 4, used a peak learning rate of 3e-3 and is in
`Models/aurora_t0/`. The Phase 5 experiments found 1e-2 better, so this
card describes the retrained model.

## Results

Next-token loss on the **test** text (lower is better; perplexity is
e^loss, roughly "how many tokens the model is choosing between"):

| | Test loss | Perplexity |
|---|---|---|
| Uniform (random guess) | 6.238 | 512 |
| Unigram (token frequency) | 5.263 | 193 |
| Bigram (previous token only) | 3.699 | 40.4 |
| AURORA-T0, Phase 4 (learning rate 3e-3) | 3.388 | 29.6 |
| **AURORA-T0** (learning rate 1e-2) | **3.354** | **28.6** |

Generated text: 20 prompts of 16 tokens from the validation text, 100
new tokens each (top-k off).

| | Real words | Distinct word pairs | Repeated 4-grams | Copied 6-grams |
|---|---|---|---|---|
| Real book text | 92.9 % | 98.5 % | 0 % | 0.5 % |
| Bigram baseline | 62.2 % | 99.9 % | 0 % | 0 % |
| T0, greedy (temperature 0) | 99.3 % | 8.8 % | 90.6 % | 0 % |
| **T0, temperature 0.5** | **85.8 %** | **95.8 %** | **0 %** | **0 %** |
| T0, temperature 0.8 | 79.2 % | 99.5 % | 0 % | 0 % |
| T0, temperature 1.0 | 69.9 % | 99.8 % | 0 % | 0 % |

What the columns mean:
- **Real words:** share of generated words that occur in the training text.
- **Distinct word pairs:** low means repetitive.
- **Repeated 4-grams:** 4-word sequences used earlier in the same sample.
- **Copied 6-grams:** 6-word sequences that appear word for word in the
  training text.

Example (temperature 0.5, seed 3; the prompt is in bold):

> **“Watson,” said Holmes,** ‘that the morning, and then, and well. I
> pault. There is the positations, and I can find his business of the
> most a light. To side the business, which was a remarks, and the h

## What it has learned

- Common English words, spelling and punctuation. At temperature 0.5,
  86 % of its words are real, against 62 % for the bigram baseline.
- The book's surface form: dialogue in curly quotes, "said Holmes",
  paragraph breaks, Victorian vocabulary ("business", "remarks").
- To generate rather than recite: no 6-word sequence was copied from the
  book.

## Limitations

- **No grammar or meaning beyond a few words.** Sentences fall apart
  after 3–5 words. It invents words ("positations", "pault").
- **Short memory.** It sees only the last 32 tokens (about 80
  characters), so it can't keep a topic, a character or a sentence going.
- **Greedy decoding loops** ("and then, and then, …"). Use sampling;
  temperature 0.5 balances real words against repetition best.
- **One book of training text:** one author's style and vocabulary, and
  English only. Other languages and modern words come out as byte
  fragments.
- **Mild overfitting:** training loss is 3.20 against validation 3.36.
  More text would help more than more steps.
- **No knowledge, instructions or safety behaviour.** It continues text;
  it can't answer questions or follow requests. It must not be connected
  to the assistant as a conversational model.
- **Small measurements:** the generation metrics come from 20 prompts,
  and the Phase 5 experiments ran each variant once.
- **Not the best architecture at this size.** In a five-seed experiment
  (Phase 16), an attention-free gated linear recurrence with the same
  number of parameters reached a 0.077 nats lower validation loss, confirmed
  on test; repeated at T0's own learning rate (1e-2), it was still 0.051
  nats lower on every seed. See [RESEARCH.md](RESEARCH.md) and [the claims register](research/CLAIMS.md).

## Running it

With AURORA's own runtime (no Ollama):

```
.venv\Scripts\python.exe -m aurora_core.runtime --model Models\aurora_t0_lr1e-2\best.npz --prompt "Sherlock Holmes"
.venv\Scripts\python.exe -m aurora_core.runtime --model Models\aurora_t0_lr1e-2\best.npz --interactive
```

## Intended use

A research milestone. It shows that AURORA's own mathematics, tokenizer,
backpropagation and training produce a working language model, and it
gives later models (T1 and beyond) a baseline to beat.
