# 0004 — Replay delivery guarantees and checkpoints

- Status: **accepted**
- Date: 2026-09-28

## Context

A replay must resume where it stopped, with no duplicates and no skips (ARCHITECTURE §4.3). A graceful stop, whether Ctrl+C or the SIGTERM that Kubernetes sends on eviction, can flush the sink and then write a checkpoint, so it resumes exactly. A hard crash (SIGKILL, out of memory, power loss) cannot update the output and the checkpoint atomically when the output is stdout.

## Options

1. **At least once after a crash.** A checkpoint is written every N samples and whenever the stream pauses on a gap. A graceful stop is exact; after a crash, at most N samples are repeated and none is skipped.
2. **A checkpoint after every sample.** At most one duplicate even after a crash, but throughput is bound by the disk. On the reference workstation this measured ~2,400 atomic writes/s, ~960 with fsync, below the 5–7k samples/s of OPSSAT-AD's dense stretches at 1000×.
3. **A sequence number on every record.** Consumers drop duplicates themselves. This changes the output format.

## Decision

Option 1. N (`every_samples`) and the pause threshold (`min_idle_s`) are set in `configs/replay/*.yaml`. A checkpoint is written only after the sink has flushed. A completed replay deletes its checkpoint, so the next run starts from the beginning; `--restart` discards the checkpoint of an unfinished one.

**Requirement on everything downstream: idempotence under the key `(mission, channel, mission_ts)`.** Consuming a sample twice must have the same effect as consuming it once. This is what makes at-least-once delivery safe. It becomes critical in M2, where the bus and the stores will see the repeats. It is written now so that nothing built before then relies on exactly-once delivery.

## Consequences

- A consumer of the M1 JSONL stream may see up to N repeated samples after a crash; idempotence on `(mission, channel, mission_ts)` makes them harmless. With Redpanda in M2, exactly-once becomes possible through transactions; that is a separate decision.
- Within a channel the stream's mission time strictly increases, and a repeat can only come from resuming at an earlier checkpoint. A consumer can therefore recognise a repeat without remembering every key: it is any sample whose `mission_ts` does not exceed the last one processed for its `(mission, channel)`. The detect pipeline (M3) drops those.
- A checkpoint holds the last sample published, as `(mission_ts, channel)`, and the mission time reached. A replay stopped halfway through a gap resumes with only the rest of that gap.
- The checkpoint is bound to a fingerprint of the data version, the window and the seed. Resuming with any of them changed is refused; changing the speed is allowed.
- A replay must run as a job, not as an always-restarting service: a supervisor that restarts a completed replay starts it over.
