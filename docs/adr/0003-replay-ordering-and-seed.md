# 0003 — Replay ordering and the seed

- Status: **accepted**
- Date: 2026-09-28

## Context

ARCHITECTURE §4.3 requires that the same data version, the same window and the same seed produce an identical stream: the same samples, in the same order. The speed factor is not part of that identity; it changes only when samples are published. Yet a replay has nothing random in it. Within a channel, mission time fixes the order. Across channels, many samples share a mission timestamp (86–99 % of timestamps on OPSSAT-AD), and something has to decide which of them goes first. The archive does not record the order in which they reached the ground.

## Options

1. **The seed orders simultaneous samples.** A seed-derived permutation of the channels breaks ties. The seed then has a real effect, and changing it tests whether anything downstream depends on the interleaving.
2. **Channel id order, seed recorded only.** Ties break alphabetically. The seed is logged and fingerprinted but does nothing until something stochastic exists, such as transport jitter or fault injection.

## Decision

Option 1. Stream order is `(mission_ts, rank(channel))`. The rank comes from SHA-256 of the seed and the channel id rather than from `random.shuffle`, whose output Python does not promise to keep across versions. Order within a channel never depends on the seed.

Wherever the project claims determinism, the claim carries this scope: **same data version, same window, same seed ⇒ the same samples in the same order**. The speed factor is outside the scope. Two runs are byte-identical only when they also share the wall clock, because `wall_ts` records when a sample was published.

## Consequences

- Replaying with several seeds shows whether a detector's results depend on the interleaving of simultaneous samples, at no cost.
- The stream's identity is the data version, the window and the seed, and nothing else. A checkpoint written for one identity refuses to resume a replay with another. A different speed resumes the same stream.
- Downstream code must not assume any order among samples that share a mission timestamp.
