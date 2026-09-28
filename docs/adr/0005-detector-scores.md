# 0005 — Detector scores

- Status: **accepted**
- Date: 2026-09-28

## Context

Every detector, from limit checking (R0) to the neural models (R2, R3), feeds the same arbiter through the same interface: a sample goes in, a score or nothing comes out. That interface must not be shaped around thresholds, or the models will not fit it. The arbiter needs to know when a detector is alarming. M4 needs graded scores, to sweep thresholds, and a way to measure how much of the stream was actually monitored.

## Options

1. **A graded score plus the detector's own decision.** Every score carries a detector-internal `value`, a `firing` flag and the `onset` of the alarm condition. The detector sets `firing` itself: after persistence and hysteresis for R0, against a calibrated threshold for a model.
2. **A score only while alarming.** The detector emits nothing unless its alarm condition holds. Simpler, but the models lose the continuous score that M4's curves need.
3. **Scores only, thresholds in the arbiter.** Detectors never decide; the arbiter thresholds every detector's scores. Persistence and hysteresis would move out of the limits configuration into the arbiter.

## Decision

Option 1, with four conditions.

1. **`value` is internal to its detector.** It is not comparable across detectors. Comparing them requires an explicit calibration, which does not exist yet. The type's docstring says so.
2. **Nothing is not zero.** A detector with no opinion on a sample (warm-up, a gap, too little data, a channel it does not check) emits no score. A sample it evaluated and found nominal gets `value = 0`. M4 measures coverage from that difference: how much of the stream was actually monitored.
3. **The threshold is not in the score.** The hash of the detector's configuration appears once, in the run header. An M4 threshold sweep stays reproducible without the thresholds weighing down every score.
4. **Severity is not in the score.** The arbiter alone assigns it, so a single place decides.

`Score` joins `groundhog.schema` with these fields, all required:

- `detector`, `mission`, `channel`, `mission_ts`;
- `value`: finite and at least 0;
- `firing`;
- `onset`: in mission time, given exactly when the score is firing, and never later than the sample;
- `quality`: the worst quality among the samples the score used.

## Consequences

- The `Detector` protocol (`groundhog.detectors.base`) answers each sample with `Score | None` and keeps its state per `(mission, channel)`, with `reset()` to clear it. It receives one sample at a time, in stream order, and nothing after it; tests check that no answer changes when later samples change.
- `Quality` gains an explicit ranking, `ok` < `stale` < `gap_filled` < `suspect`, so the worst quality flows from samples to scores to events.
- Severity bands in the arbiter are set per detector, because values are not comparable across detectors.
- Coverage needs no extra field: per channel, it is the number of samples that received a score over the number of samples seen.
