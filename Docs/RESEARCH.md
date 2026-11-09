# AURORA research

Blueprint Phase 16: AURORA as a long-term research platform. The rule:
**every claimed improvement is supported by a reproducible experiment.**
This page is the protocol; [CLAIMS.md](research/CLAIMS.md) lists what has
been shown, and each experiment has its own folder under
[docs/research/](research/).

## Results so far

### architecture_v1: one-change variants of T0

[Report](research/architecture_v1/report.md) · [runs](research/architecture_v1/runs.jsonl) ·
[specification](../aurora_core/research/specs/architecture_v1.json). 20 runs (4 architectures × 5 seeds),
2,000 steps of 1,024 tokens each, *The Adventures of Sherlock Holmes*, about 30,000 parameters each.

| Variant | Best validation loss (mean ± sd) | Difference vs T0 [95 % CI] | Seeds better | Test loss | Finding |
|---|---|---|---|---|---|
| T0 (attention, layer norm, GELU) | 3.626 ± 0.013 | — | — | 3.592 | baseline |
| RMSNorm | 3.615 ± 0.016 | −0.011 [−0.024, +0.002] | 5 of 5 | 3.591 | no detectable difference |
| SwiGLU | 3.605 ± 0.019 | −0.021 [−0.048, +0.005] | 4 of 5 | 3.574 | no detectable difference |
| **Gated linear recurrence** (no attention) | **3.549 ± 0.009** | **−0.077 [−0.102, −0.053]** | **5 of 5** | **3.520** | **improvement, confirmed on test** |

**The hypothesis was partly wrong.** It predicted RMSNorm and SwiGLU would make little difference (they
didn't make a detectable one) and that the recurrence would be *slightly worse* than attention. Instead the
attention-free recurrence was better on every seed, by 0.077 nats (about 7.4 % lower perplexity), and the
test text confirmed it. Before accepting this, it was checked that no variant can see future tokens (changing
later tokens changes earlier predictions by exactly 0; now a permanent test), that parameter counts match
within 0.3 %, and that the runs reproduce bit for bit from scratch.

A likely explanation, *not* tested here: at this size, a built-in bias towards recent tokens (each channel
is a running average with a memory of about 2 to 32 tokens) is easier to use than attention, which must
learn from scratch where to look. Larger models and longer contexts are where attention is known to pay off,
so this result says nothing about them.

What it does and doesn't show:

- Shown: at T0's size, budget and data, the gated linear recurrence reaches a lower loss than attention.
- Not shown: anything about larger models, longer training, longer contexts, other text, or text *quality*
  (only loss was measured). The recurrence also isn't usable by the AURORA Core Runtime or the assistant
  yet: it is a research model.
- **A correction to the specification:** its limitations say hyperparameters "were tuned for T0 in Phase 5".
  In fact every run used the trainer's defaults (maximum learning rate 3e-3), the same as Phase 5's
  baseline, not the 1e-2 Phase 5 found better. The specification is left as it was recorded; the claim is
  about this setting. Repeating the comparison at 1e-2 is the obvious next experiment.

Reproducibility, beyond the re-runs in the report: T0 with seed 0 here gave a best validation loss of
3.6264810752868653, the same to every digit as Phase 5's baseline run three days earlier, with different
code around it (the research runner instead of training/experiments.py).

### Earlier findings

Phase 5's sweep (`training/experiments.py`, [AURORA_CORE.md](AURORA_CORE.md)) ran each variant with a
single seed. Its conclusions (width helps most, learning rate 1e-2 beats 3e-3) were reasonable guides, but
they are **not claims under this protocol** until they are repeated with several seeds.

## The protocol

1. **Write the specification first** (`aurora_core/research/specs/<name>.json`):
   the question, a hypothesis, the data file with its SHA-256 and source,
   the baseline, the variants (one change each), the seeds (at least 3;
   5 is the default) and the decision rule. It is committed before the
   experiment runs.
2. **Compare like with like.** Variants are parameter-matched to the
   baseline (`arch.matched`), use the same tokenizer (trained once, on
   the training part of the text only), the same number of steps and
   tokens per step, and the same hyperparameters. A variant and the
   baseline with the same seed see exactly the same batches.
3. **Decide on validation, confirm on test.** A variant is an
   improvement only if the 95 % confidence interval of its mean paired
   difference in best validation loss lies entirely below zero and the
   improvement is at least the specification's `min_effect`. The test
   text is used once, to confirm a claimed improvement. Anything else is
   reported as *no detectable difference* or *worse*, and recorded in
   the claims register too: negative results count.
4. **Record everything.** Each run stores its configuration, loss
   history, best validation loss, test loss, time and a SHA-256 of its
   final weights; the experiment stores the environment (Python, NumPy,
   CPU, OS), the git commit and a fingerprint of all aurora_core source
   code. Runs made with different code or data are never mixed.
5. **Re-run from scratch.** `verify` trains a recorded run again and
   checks that the weights are bit-for-bit identical. The report lists
   every such check.

## Commands

```
python -m aurora_core.research run    --spec aurora_core/research/specs/architecture_v1.json --data Data/pg1661.txt
python -m aurora_core.research verify --spec aurora_core/research/specs/architecture_v1.json --data Data/pg1661.txt --run "T0 | seed 0"
python -m aurora_core.research report --spec aurora_core/research/specs/architecture_v1.json --data Data/pg1661.txt
python -m aurora_core.research claims
```

`run` can be stopped and started again: finished runs are kept (seeds are
run in order, so complete pairs accumulate) and only missing ones are
trained, as long as the code and data are unchanged. `--limit N` runs at
most N new runs. The data file isn't in git (it is a public Project
Gutenberg book; the specification gives its address and SHA-256, and a
different file is refused).

## Research models (`aurora_core/research/arch.py`)

`VariantModel(ArchConfig(model=T0Config(...), norm=..., ffn=..., mixer=...))`:

| Option | Values | What changes |
|---|---|---|
| `norm` | `layer` (T0), `rms` | RMSNorm (no mean subtraction, no shift) everywhere T0 uses layer norm |
| `ffn` | `gelu` (T0), `swiglu` | `silu(x Wg) * (x Wu) Wd`, width chosen to match T0's parameter count |
| `mixer` | `attention` (T0), `recurrent` | A gated linear recurrence instead of attention: each channel is a running average with its own learned memory length (about 2 to 32 tokens at the start), gated by the input. O(T) per sequence and a fixed-size state when generating, instead of attention's O(T²) and growing key/value cache. Feed-forward widened to match T0's parameter count. |

With the defaults it *is* AURORA-T0: the same parameter names, the same
initial weights for a seed, identical logits and gradients (tested). Each
variant's backward pass is checked against numerical gradients for every
parameter. The variants are research models only: the AURORA Core
Runtime and the Command Centre still run T0 checkpoints.

## Adding an experiment

1. Copy `specs/architecture_v1.json`, change the question, hypothesis,
   variants and decision rule. Commit it.
2. Run it (on this laptop, a T0-sized run of 2,000 steps takes about
   3.5 minutes; budget seeds × variants runs).
3. `verify` at least one run per variant you want to claim something about.
4. `claims`, then commit the experiment's folder in `docs/research/`.

New kinds of variants go in `arch.py`, with a gradient check in
`Tests/test_core_research.py` before any experiment uses them.
