# 0002 — Readings: samples before publication

- Status: **accepted**
- Date: 2026-09-28

## Context

`Sample` requires `wall_ts`, the moment a sample entered the live plane. Detectors use it to measure latency, and it is only meaningful at publication. A source adapter reads an archive and cannot know it; only the replay engine, or a live feed, can. The normalised zone of the data lake (ARCHITECTURE §4.1) has the same problem, since `wall_ts` means nothing for a value at rest.

## Options

1. **A new type in `groundhog.schema`.** `Reading` carries the fields of `Sample` except `wall_ts`. Adapters emit readings; the publisher stamps `wall_ts` and emits a `Sample`. `Sample` is unchanged.
2. **A placeholder `wall_ts`.** Adapters emit `Sample` with `wall_ts` set to the time of reading, and the publisher overwrites it. There is no schema change, but a meaningless `wall_ts` travels until publication, and nothing stops someone from measuring latency with it.
3. **An optional `wall_ts`.** `Sample.wall_ts` becomes `datetime | None`. This weakens the contract: every consumer has to handle `None`.

## Decision

Option 1. `Reading.publish(wall_ts)` is the only way a reading becomes a sample.

## Consequences

- Two types share seven fields. A test pins `Reading` to be exactly `Sample` minus `wall_ts`, with the same types, defaults, descriptions and constraints, so they cannot drift apart.
- Nothing after publication can hold a sample without a real publication time.
- The normalised zone of the lake (M2) stores readings, not samples.
