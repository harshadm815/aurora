# Experiment: architecture_v1

**Question.** At AURORA-T0's size (about 30,000 parameters) and training budget (2,000 steps of 1,024 tokens), does replacing one part of the Transformer give a lower validation loss: RMSNorm instead of layer norm, a SwiGLU feed-forward instead of GELU, or an attention-free gated linear recurrence instead of attention?

**Hypothesis (written before running).** RMSNorm and SwiGLU, which help large models, make little or no difference at this size. The gated linear recurrence, which only keeps a running summary per channel instead of looking back at every token, is worse than attention but not by much, because 32-token contexts of English text are dominated by nearby tokens.

**Decision rule (written before running).** A variant is an improvement only if the 95 % confidence interval of its mean paired difference in best validation loss against `T0` lies entirely below zero and the mean improvement is at least 0.01 nats; the test text is then used once to confirm it. Otherwise the result is reported as no detectable difference (or worse).

**Status.** Complete: 20 runs, seeds [0, 1, 2, 3, 4], 2000 steps of 1024 tokens each.

## Results

| Variant | Parameters | Best validation loss (mean ± sd) | Difference vs baseline [95 % CI] | Seeds better | Test loss (mean) | Verdict |
|---|---|---|---|---|---|---|
| T0 | 30,176 | 3.626 ± 0.013 | — | — | 3.592 | baseline |
| RMSNorm | 30,080 | 3.615 ± 0.016 | -0.011 [-0.024, +0.002] | 5 of 5 | 3.591 | no detectable difference |
| SwiGLU | 30,272 | 3.605 ± 0.019 | -0.021 [-0.048, +0.005] | 4 of 5 | 3.574 | no detectable difference |
| Recurrent | 30,160 | 3.549 ± 0.009 | -0.077 [-0.102, -0.053] | 5 of 5 | 3.520 | improvement (confirmed on test) |

The baseline's test loss is -0.106 nats from the bigram baseline (3.699) [95 % CI -0.118, -0.095]: it beats the bigram baseline.

### Per seed (best validation loss)

| Variant | seed 0 | seed 1 | seed 2 | seed 3 | seed 4 |
|---|---|---|---|---|---|
| T0 | 3.626 | 3.616 | 3.645 | 3.631 | 3.613 |
| RMSNorm | 3.597 | 3.613 | 3.639 | 3.620 | 3.607 |
| SwiGLU | 3.586 | 3.628 | 3.608 | 3.619 | 3.585 |
| Recurrent | 3.538 | 3.560 | 3.546 | 3.546 | 3.557 |

## Reproducibility

- Data: `Data/pg1661.txt` SHA-256 `922e2a12ccb43a4c9544c260b2166c6ad2097aeb5957faeee113f173bb857cd0` (Project Gutenberg eBook #1661, The Adventures of Sherlock Holmes, https://www.gutenberg.org/ebooks/1661.txt.utf-8)
- Tokenizer: byte-level BPE, 512 tokens, trained on the training part only; SHA-256 `445a0a239facec5eb45c08ed4239156f5c3e999444febb42d6fb2300b1e5a32d`
- Tokens: {'train': 238215, 'validation': 13830, 'test': 12459}
- Code: aurora_core fingerprint `a73cac9840a36ac6…`, git commit `de6658d1482b`
- Environment: Python 3.14.2, NumPy 2.5.4, Windows-11-10.0.26200-SP0, Intel64 Family 6 Model 142 Stepping 10, GenuineIntel
- Every run's configuration, loss history and a SHA-256 of its final weights are in `runs.jsonl`.

Reproduce:

```
python -m aurora_core.research run --spec aurora_core/research/specs/architecture_v1.json --data Data/pg1661.txt
python -m aurora_core.research verify --spec aurora_core/research/specs/architecture_v1.json --data Data/pg1661.txt --run "T0 | seed 0"
```

The runs were made with the code at git commit `de6658d1482b` (`git checkout de6658d1482b` for exactly that code). A re-run with later code is marked below; it still has to give identical weights, which shows training itself is unchanged.

### Re-runs from scratch

| Run | Identical weights | Recorded validation loss | Reproduced | Code |
|---|---|---|---|---|
| Recurrent \| seed 3 | yes | 3.545721 | 3.545721 | as recorded |
| T0 \| seed 0 | yes | 3.626481 | 3.626481 | as recorded |
| SwiGLU \| seed 4 | yes | 3.585035 | 3.585035 | later version |

## Limitations

- One small model size, one training budget and one book: results may not hold for larger models, longer training or other text.
- Five seeds detect differences of a few hundredths of a nat; smaller real differences would show as 'no detectable difference'.
- Hyperparameters (learning rate etc.) were tuned for T0 in Phase 5 and are reused unchanged for every variant, which may favour T0.
- Results are exact on this machine; another CPU or NumPy version may give slightly different numbers (the analysis, not the bits, should then agree).
