# Experiment: architecture_lr1e-2

**Question.** Does architecture_v1's result hold at the learning rate Phase 5 found better for T0 (maximum 1e-2, falling to 1e-3, instead of 3e-3 to 3e-4)? Same data, tokenizer, model size, steps, seeds and variants; only the learning rate changes.

**Hypothesis (written before running).** T0 improves more from the higher learning rate than the variants, because Phase 5 found 1e-2 better for T0 specifically. The gated linear recurrence stays better than attention but by less than architecture_v1's 0.077 nats. RMSNorm and SwiGLU still show no detectable difference.

**Decision rule (written before running).** A variant is an improvement only if the 95 % confidence interval of its mean paired difference in best validation loss against `T0` lies entirely below zero and the mean improvement is at least 0.01 nats; the test text is then used once to confirm it. Otherwise the result is reported as no detectable difference (or worse).

**Status.** Complete: 20 runs, seeds [0, 1, 2, 3, 4], 2000 steps of 1024 tokens each.

## Results

| Variant | Parameters | Best validation loss (mean ± sd) | Difference vs baseline [95 % CI] | Seeds better | Test loss (mean) | Verdict |
|---|---|---|---|---|---|---|
| T0 | 30,176 | 3.459 ± 0.025 | — | — | 3.440 | baseline |
| RMSNorm | 30,080 | 3.454 ± 0.008 | -0.006 [-0.028, +0.017] | 2 of 5 | 3.437 | no detectable difference |
| SwiGLU | 30,272 | 3.464 ± 0.010 | +0.005 [-0.028, +0.038] | 2 of 5 | 3.458 | no detectable difference |
| Recurrent | 30,160 | 3.408 ± 0.007 | -0.051 [-0.080, -0.022] | 5 of 5 | 3.388 | improvement (confirmed on test) |

The baseline's test loss is -0.259 nats from the bigram baseline (3.699) [95 % CI -0.283, -0.235]: it beats the bigram baseline.

### Per seed (best validation loss)

| Variant | seed 0 | seed 1 | seed 2 | seed 3 | seed 4 |
|---|---|---|---|---|---|
| T0 | 3.438 | 3.439 | 3.455 | 3.465 | 3.500 |
| RMSNorm | 3.444 | 3.447 | 3.456 | 3.458 | 3.463 |
| SwiGLU | 3.450 | 3.475 | 3.470 | 3.460 | 3.465 |
| Recurrent | 3.401 | 3.405 | 3.407 | 3.421 | 3.408 |

## Reproducibility

- Data: `Data/pg1661.txt` SHA-256 `922e2a12ccb43a4c9544c260b2166c6ad2097aeb5957faeee113f173bb857cd0` (Project Gutenberg eBook #1661, The Adventures of Sherlock Holmes, https://www.gutenberg.org/ebooks/1661.txt.utf-8)
- Tokenizer: byte-level BPE, 512 tokens, trained on the training part only; SHA-256 `445a0a239facec5eb45c08ed4239156f5c3e999444febb42d6fb2300b1e5a32d`
- Tokens: {'train': 238215, 'validation': 13830, 'test': 12459}
- Code: aurora_core fingerprint `0627e58ee757cd79…`, git commit `1e1e9ecba7ad`
- Environment: Python 3.14.2, NumPy 2.5.4, Windows-11-10.0.26200-SP0, Intel64 Family 6 Model 142 Stepping 10, GenuineIntel
- Every run's configuration, loss history and a SHA-256 of its final weights are in `runs.jsonl`.

Reproduce:

```
python -m aurora_core.research run --spec aurora_core/research/specs/architecture_lr1e-2.json --data Data/pg1661.txt
python -m aurora_core.research verify --spec aurora_core/research/specs/architecture_lr1e-2.json --data Data/pg1661.txt --run "T0 | seed 0"
```

The runs were made with the code at git commit `1e1e9ecba7ad` (`git checkout 1e1e9ecba7ad` for exactly that code). A re-run with later code is marked below; it still has to give identical weights, which shows training itself is unchanged.

### Re-runs from scratch

| Run | Identical weights | Recorded validation loss | Reproduced | Code |
|---|---|---|---|---|
| Recurrent \| seed 3 | yes | 3.420544 | 3.420544 | as recorded |
| T0 \| seed 4 | yes | 3.499543 | 3.499543 | as recorded |

## Limitations

- One small model size, one training budget and one book: results may not hold for larger models, longer training or other text.
- Five seeds detect differences of a few hundredths of a nat; smaller real differences would show as 'no detectable difference'.
- The learning rate 1e-2 was chosen in Phase 5 from a single-seed comparison with T0 only; it may still not suit every variant equally. Only two learning rates have now been tried.
- Results are exact on this machine; another CPU or NumPy version may give slightly different numbers (the analysis, not the bits, should then agree).
