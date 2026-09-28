# 0001 — Train/test split for OPSSAT-AD

- Status: **proposed**
- Date: 2026-09-28

## Context

OPSSAT-AD ships an official split. The `train` column marks 1,594 segments for training and 529 for testing, stratified by label (20.1 % and 21.4 % anomalous). The published baselines, 30 models in the benchmark paper, all use it.

The split is not chronological. On every channel, train and test segments alternate across the whole period. In 117 of the 161 continuous runs that contain more than one segment, adjacent windows of the same run land on opposite sides.

CONTRIBUTING.md rejects temporal leakage: shuffled splits on time series, and scalers or thresholds fitted outside the training period (see also ARCHITECTURE §4.6). Adjacent windows of one run are strongly correlated. A detector evaluated on the official split is therefore partly scored on data it has effectively seen, and every metric it reports is inflated.

A chronological split is not straightforward on this dataset either:

| Period | Segments | Anomalous | Samples |
|---|---|---|---|
| 4 Jan – 11 Feb 2022 | 1,026 | 106 (10.3 %) | 45,361 |
| 1–2 Jun 2022 (one night) | 1,097 | 328 (29.9 %) | 258,132 |

A cut between February and June leaves 15 % of the samples for training and shifts prevalence from 10 % to 30 %. `CADC0886` and `CADC0890` exist only in January.

## Options

1. **Official split only.** Directly comparable with the published baselines. It breaks the rule against temporal leakage and inflates every number Groundhog reports.
2. **Chronological split only.** Honest, but comparable with no published result. The cut point has to be chosen with care, most likely inside the 1–2 June night so that both sides contain the June regime. An embargo interval at the cut keeps adjacent windows apart.
3. **Both, clearly labelled.** Groundhog's own metrics use a chronological split with an embargo. The official split is used only for a separate comparison table, marked as such and never presented as the headline result.

## Decision

Proposed: option 3. The cut point and the embargo length belong in the evaluation harness configuration (M4), where they can be versioned with the results.

## Consequences

- Two result tables for OPSSAT-AD: the chronological one is the result; the official-split one exists only for comparison with the literature.
- The replay engine is unaffected: it streams all segments regardless of split. The split is applied only when labels are joined to detections.
- Channels present on only one side of the cut are reported separately rather than silently dropped.
