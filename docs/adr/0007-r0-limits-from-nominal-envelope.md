# 0007 — R0 limits for a dataset without operational limits

- Status: **accepted**
- Date: 2026-09-28

## Context

R0 checks values against limits, and OPSSAT-AD carries none: no units, no thresholds, and the meaning of the channels is not in the files. Numbers chosen by hand would put invented values into every R0 result.

## Options

1. **The envelope of the nominal training data.** Per channel, the minimum and maximum of the samples labelled nominal in the training window, each side pushed out by a margin set in configuration.
2. **Percentiles of all training data.** It needs no labels, but anomalies in the training window widen the limits.
3. **No limits in M3.** Every channel is declared unchecked, and R0 runs on synthetic data only.

## Decision

Option 1.

- **Derivation.** `scripts/derive_limits.py` (`make limits`) derives `configs/limits/opssat.yaml` from `configs/limits/opssat.derive.yaml`. The output records its provenance: the data MD5, the spec hash, the training window, the rule, the margin and the number of nominal samples per channel. The same data and spec give the same bytes, and `--check` fails if the committed file differs.
- **Training window.** Provisionally, everything before 1 March 2022, which is January–February, until M4 fixes the folds of ADR 0001. M4 refits the limits in every fold with the same code, on that fold's training data only.
- **Channels that get no limits.** Categorical channels get as allowed states the codes seen in nominal training. Telecommands, channels without nominal training data and constant channels are written as `check: none`, with the reason.
- **Rate limits** are not derived: `max_rate` stays null on OPSSAT-AD.
- **Starting values.** The margin starts at 10 %; the alarm policy at hysteresis 0.05, persistence 3 samples and a maximum gap of 60 s. They are starting points, not results: M4 weighs them against alarm noise.

## Consequences

- **What R0 can catch.** On OPSSAT-AD it flags values outside what the spacecraft did nominally in January–February, so it can only catch anomalies that leave that envelope.
- **Dependence on labels.** The envelope is only as good as the labels: a mislabelled anomaly in the training window widens it.
- **Unreachable limits.** `CADC0884` and `CADC0892` stay within 0 and 1.5708 (π/2) across the whole dataset, inside their envelope of −0.157 to 1.728. R0 cannot fire on them. This is the kind of finding M4 reports.
- **Labels in fitting.** Fitting on the labels of the training window is legitimate, because they belong to the training period. The labels of the test period never reach the fitting.
- **R0 is a fitted detector.** In M4 its limits are derived in every fold from that fold's training data, with the embargo applied. The harness refuses to evaluate a fold whose limits file records a training window that overlaps the fold's test period or the embargo before it.
- **Not an industrial baseline.** These limits do not come from mission documents. Every report says so: they approximate the industrial baseline, reasonably, but are not it.
