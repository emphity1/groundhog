# 0001 — Train/test split for OPSSAT-AD

- Status: **accepted**
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

Labels come per segment, and consecutive anomalous segments of one continuous run describe one anomaly. Grouped that way, the 434 anomalous segments form **320 labelled events**: median duration 277 s, 99th percentile 2,747 s, longest 6,545 s (14 segments of `CADC0874`, 2 June 06:32–08:21).

## Options

1. **Official split only.** Directly comparable with the published baselines. It breaks the rule against temporal leakage and inflates every number Groundhog reports.
2. **Chronological split only.** Honest, but comparable with no published result. The cut point has to be chosen with care, most likely inside the 1–2 June night so that both sides contain the June regime. An embargo interval at the cut keeps adjacent windows apart.
3. **Both, clearly labelled.** Groundhog's own metrics use a chronological split with an embargo. The official split is used only for a separate comparison table, marked as such and never presented as the headline result.

## Decision

Option 3, under four conditions.

1. **The embargo is computed, never chosen.** Between the end of a training period and the start of the test period it follows, the embargo lasts at least the longest lookback of the detectors under evaluation plus the longest labelled event. The harness computes both from the data and the detector configurations; nobody types the number. On OPSSAT-AD the event term alone is 6,545 s. It is taken over all labels: it is a safeguard of the protocol, not a parameter of any model, and the conservative maximum is what keeps an event from straddling the cut.
2. **The official split exists only for comparison with the literature.** It is always reported next to Groundhog's own split and never as the headline number.
3. **OPSSAT-AD is the development dataset.** Headline results will come from ESA-ADB under its own protocol. Nothing here is tuned beyond this split.
4. **Walk-forward with a rolling origin, because the data allow it.** A fold is usable when its test block holds at least 10 labelled events and every channel it tests has nominal training data before the embargo. With the test blocks below, five folds qualify:

   | Test block | Segments | Labelled events | Channels | Training before it |
   |---|---|---|---|---|
   | 1–11 Feb | 46 | 14 | 2 | January: 262 and 331 nominal segments of the two channels |
   | 1 Jun 23:00 – 2 Jun 03:00 | 289 | 44 | 7 | January–February, every channel |
   | 2 Jun 03:00 – 07:00 | 381 | 81 | 7 | Everything before 03:00 minus the embargo |
   | 2 Jun 07:00 – 11:00 | 269 | 65 | 7 | Everything before 07:00 minus the embargo |
   | 2 Jun 11:00 – 16:00 | 158 | 27 | 6 | Everything before 11:00 minus the embargo |

   January is never a test block. Its first week holds 6 events, and its last week tests 8 channels of which only 4 appear earlier. A 14-day hole separates the two. The exact block boundaries belong to the evaluation harness configuration (M4), not to this record.

## Consequences

- Every OPSSAT-AD result appears twice: the walk-forward result, which is the result, and the official-split result, labelled as a comparison with the literature.
- Four of the five test blocks come from a single night, one operating regime. Folds are therefore not independent samples of the mission, and the dispersion across folds is a lower bound of the real variance. That is acceptable for a development dataset, and every report says so.
- Anything fitted on data — scalers, thresholds, R0 limits (ADR 0007) — is refitted in every fold on that fold's training data only.
- The replay engine is unaffected: it streams all segments regardless of split. The split is applied only when labels are joined to detections.
