# 0006 — The event stream

- Status: **accepted**
- Date: 2026-09-28

## Context

M3 puts detection behind the replay with nothing but a pipe, `replay | detect`. What comes out is what an operator, and M4's harness, will read first. Four choices shape it:

- what one event covers;
- when an event is written;
- how a reader tells apart versions of the same event;
- how a file of events says which configuration produced it.

## Options

- **Scope.** (a) One event per detector per channel, as in classic limit checking, where every parameter alarms on its own. (b) Overlapping violations of one detector across channels as a single event, which is already a form of fusion.
- **Emission.** (a) Records as upserts: a record when the event opens, another when its severity rises and one when it closes, all under the same id. The last record for an id is the event, and events still open at the end are written once more with `t_end` null. (b) One record per event, written when it closes, with still-open events written at the end.
- **Run header.** (a) The first line of stdout. (b) stderr only. (c) A separate file.

## Decision

Options (a) throughout.

- **One event per `(mission, detector, channel)`.** Grouping across channels is correlation; it arrives in M5 with fusion, through the arbiter's `EventHook`.
- **Timing.** An event starts, `t_start`, at the onset of the violation that fired it. `detections[0].fired_at` is when the detector fired, and `delay_s` is the difference: the cost of persistence, on mission time. The event ends, `t_end`, at its last firing sample.
- **Records.** A record is written when the event opens (`t_end` null), when its severity rises, when it closes, and at the end of the stream for every event still open (`t_end` null). An open event is never dropped.
- **Identity.** The `id` says what the event is, `mission:detector:channel:onset`, with the onset in UTC to the microsecond. The same stream always yields the same ids. The last record for an id is the event, so writing it again is harmless (ADR 0004).
- **Severity.** It comes from the event's peak score through bands set per detector in the detect configuration, because scores are not comparable across detectors (ADR 0005). It can only rise.
- **Quality.** The worst quality among the event's scores, `ok` < `stale` < `gap_filled` < `suspect`.
- **Status.** `status` stays `new`. It belongs to the operator's workflow; whether the event is still open is what a null `t_end` says.
- **Run header.** The first line of stdout is `{"run": {...}}`. It records the tool and its version, the configuration path, the id, model version and configuration hash of each detector, and the hash of the arbiter settings. Events follow, one per line.

## Consequences

- A reader keeps the last record per `id`: counting records is not counting events, and M4 counts distinct ids.
- The operator sees an event the moment it opens, and sample-to-event latency is measurable on wall time through `created_at`.
- A file of events names the configuration that produced it. Together with the replay's stream identity (ADR 0003), a result can be regenerated from it.
- At one instant, records of different channels interleave in stream order, which follows the replay's seed.
- R0 produces classic per-parameter alarm counts: exactly the noise M4 must measure before anything tries to reduce it.
