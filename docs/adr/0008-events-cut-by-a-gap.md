# 0008 — Events cut short by a gap, in the matching rule

- Status: **accepted**
- Date: 2026-09-28

## Context

R0 resets a channel after a silence longer than `max_gap_s` (ADR 0007), and the arbiter then closes the open event at its last firing sample before the gap (ADR 0006). The annotation that event was tracking may continue after the gap. On OPSSAT-AD this is common enough to need a rule: 17 of the 320 labelled events contain a gap longer than 60 s inside a segment, between 75 and 130 s. Every segment with such a gap is labelled anomalous.

M4's matching rule has to say what a truncated event counts for, and it has to stay faithful to the ESA-ADB metrics (CONTRIBUTING.md). The ESA-ADB paper (arXiv:2406.17826) fixes three points:

- The event-wise score "does not depend on a level of overlap of detections and ground truth". Any overlap detects an event.
- "The corrected event-wise F-score counts only a single true positive even if there are multiple separated detections for the same fragment in the ground truth." Those extra detections are "redundant alarms", counted by the event-wise alarming precision `Pr_A = TP_e / (TP_e + TP_r)`.
- For timing, "the first detection is the most important one, because it already enforces an action".

## Options

1. **A truncated event counts.** It detects the annotation it overlaps, even though the annotation continues after the gap. A new event after the gap on the same annotation is a redundant alarm.
2. **A truncated event counts only if the annotation ends before the gap.** Otherwise the annotation needs a detection after the gap as well.
3. **Merge the events on both sides of a gap** before matching.

## Decision

Option 1.

- **Recall.** An annotation is one true positive if any event overlaps it, truncated or not.
- **The event after the gap.** A second event on the same annotation after the gap is neither a new true positive nor a false positive. It is a redundant alarm, `TP_r`, which lowers the alarming precision.
- **Timing.** Detection timing (ADTQC, and Groundhog's detection delay) is taken from the first event that overlaps the annotation, the truncated one if it came first.

Option 2 asks more of a detector than ESA-ADB's event-wise recall does, and numbers computed that way would not be comparable with published results. Option 3 would hide what the gap did: the operator did receive two alarms, and ESA-ADB counts redundant alarms rather than merging them away.

## Consequences

- Missing data never costs a detection: recall is not penalised for what the detector could not see.
- Missing data can cost alarm precision: the same anomaly is alarmed twice. That shows in the alarming precision and in alarms per day, which count distinct event ids (ADR 0006).
- The truncation does not change the detection delay, which is set by the first event.
- Still open, and part of the rest of M4's matching rule: which instant of an event counts as its detection start, the onset `t_start` or `detections[0].fired_at`, when the detector actually raised the alarm.
