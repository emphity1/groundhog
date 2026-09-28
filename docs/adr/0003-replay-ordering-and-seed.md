# 0003 — Replay ordering and the seed

- Status: **accepted**
- Date: 2026-09-28

## Context

ARCHITECTURE §4.3 requires that the same window, the same seed and the same configuration produce an identical stream. Yet a replay has nothing random in it. Within a channel, mission time fixes the order. Across channels, many samples share a mission timestamp (86–99 % of timestamps on OPSSAT-AD), and something has to decide which of them goes first. The archive does not record the order in which they reached the ground.

## Options

1. **The seed orders simultaneous samples.** A seed-derived permutation of the channels breaks ties. The seed then has a real effect, and changing it tests whether anything downstream depends on the interleaving.
2. **Channel id order, seed recorded only.** Ties break alphabetically. The seed is logged and fingerprinted but does nothing until something stochastic exists, such as transport jitter or fault injection.

## Decision

Option 1. Stream order is `(mission_ts, rank(channel))`. The rank comes from SHA-256 of the seed and the channel id rather than from `random.shuffle`, whose output Python does not promise to keep across versions. Order within a channel never depends on the seed.

## Consequences

- Replaying with several seeds shows whether a detector's results depend on the interleaving of simultaneous samples, at no cost.
- The seed is part of the stream's identity: a checkpoint written with one seed refuses to resume a replay with another.
- Downstream code must not assume any order among samples that share a mission timestamp.
