# SPDX-License-Identifier: Apache-2.0
"""The replay engine: an archive published as if it were arriving now.

* **Two clocks.** A sample keeps the mission time it carries and gets, as
  ``wall_ts``, the moment it is published. It is due at
  ``wall_anchor + (mission_ts - mission_anchor) / speed``. The schedule is
  absolute, so rounding never accumulates, and a late sample goes out at once:
  nothing is dropped to catch up.
* **Gap fidelity.** Silence in the archive is silence in the stream, compressed
  by the same factor as everything else. A window's start is where its stream
  starts, so any silence before the first sample is replayed too.
* **Determinism.** Same data version, same window, same seed: the same samples
  in the same order. Stream order is (mission_ts, channel rank), with the rank
  derived from the seed (ADR 0003); within a channel, order is mission time. The
  speed factor is not part of the stream's identity: it changes only wall time.
* **Checkpoint and resume.** Exact after a graceful stop, at least once after a
  crash: a checkpoint is saved only after the sink has flushed, and a completed
  replay removes it (ADR 0004).
"""

from __future__ import annotations

import hashlib
import heapq
import json
import logging
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import chain, dropwhile

from groundhog.ingest import Source
from groundhog.replay.checkpoint import Checkpoint, CheckpointError, CheckpointStore, Position
from groundhog.replay.clock import Clock
from groundhog.replay.config import CheckpointSettings, ReplaySettings
from groundhog.replay.sinks import Sink
from groundhog.schema import Reading

__all__ = [
    "CheckpointMismatchError",
    "ReplayEngine",
    "ReplayResult",
    "channel_ranks",
    "stream_fingerprint",
]

log = logging.getLogger(__name__)


class CheckpointMismatchError(CheckpointError):
    """The checkpoint was written for another stream: other data, window or seed."""


@dataclass(frozen=True)
class ReplayResult:
    published: int
    """Samples published by this run."""
    total: int
    """Samples of the stream published so far, across resumes."""
    completed: bool
    """False when the run stopped early and left a checkpoint to resume from."""


def channel_ranks(channels: Iterable[str], seed: int) -> dict[str, int]:
    """The order in which samples sharing a mission_ts are published (ADR 0003).

    SHA-256 of seed and channel rather than ``random.shuffle``, whose output Python
    does not promise to keep across versions.
    """
    ordered = sorted(channels, key=lambda c: hashlib.sha256(f"{seed}/{c}".encode()).digest())
    return {channel: rank for rank, channel in enumerate(ordered)}


def stream_fingerprint(source: Source, settings: ReplaySettings) -> str:
    """Identity of a stream: what is published and in which order, never how fast."""
    identity = {
        "source": source.describe(),
        "seed": settings.seed,
        "window": settings.window.model_dump(mode="json"),
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class ReplayEngine:
    def __init__(
        self,
        source: Source,
        sink: Sink,
        clock: Clock,
        store: CheckpointStore,
        replay: ReplaySettings,
        checkpoint: CheckpointSettings,
    ) -> None:
        self._source = source
        self._sink = sink
        self._clock = clock
        self._store = store
        self._speed = replay.speed
        self._seed = replay.seed
        self._window = replay.window
        self._every = checkpoint.every_samples
        self._min_idle = timedelta(seconds=checkpoint.min_idle_s)
        self._ranks = channel_ranks(source.channels(), replay.seed)
        self.fingerprint = stream_fingerprint(source, replay)

    def run(self, stop: threading.Event) -> ReplayResult:
        """Publish the stream, from the checkpoint if there is one, until it ends or
        ``stop`` is set. The sink is flushed before returning, either way."""
        resumed = self._store.load()
        if resumed is not None and resumed.stream != self.fingerprint:
            raise CheckpointMismatchError(
                "the checkpoint belongs to another stream: "
                "the data, the window or the seed changed since it was written"
            )
        last = resumed.last if resumed else None
        total = resumed.published if resumed else 0
        before = total

        pending = self._ordered(after=last)
        first = next(pending, None)
        if first is None:
            self._store.clear()
            log.info("stream %s: nothing left to publish", self.fingerprint[:12])
            return ReplayResult(published=0, total=total, completed=True)

        if resumed is not None:
            mission_anchor = resumed.mission_clock
        elif self._window.start is not None:
            mission_anchor = self._window.start
        else:
            mission_anchor = first.mission_ts
        wall_anchor = self._clock.now()
        mission_clock = mission_anchor
        log.info(
            "stream %s: %s at mission time %s, speed %gx, seed %d",
            self.fingerprint[:12],
            f"resuming after {total} samples" if resumed else "starting",
            mission_anchor.isoformat(),
            self._speed,
            self._seed,
        )

        for reading in chain((first,), pending):
            if stop.is_set():
                break
            due = wall_anchor + (reading.mission_ts - mission_anchor) / self._speed
            wait = due - self._clock.now()
            if wait > timedelta(0):
                self._sink.flush()
                if wait >= self._min_idle:
                    self._save(total, last, mission_clock)
                if self._clock.sleep_until(due, stop):
                    # Stopped inside the wait: keep how far mission time got, so the
                    # resumed replay waits only for what is left of the gap.
                    reached = mission_anchor + (self._clock.now() - wall_anchor) * self._speed
                    mission_clock = min(max(reached, mission_clock), reading.mission_ts)
                    break
            self._sink.write(reading.publish(wall_ts=self._clock.now()))
            total += 1
            last = Position(mission_ts=reading.mission_ts, channel=reading.channel)
            mission_clock = reading.mission_ts
            if total % self._every == 0:
                self._sink.flush()
                self._save(total, last, mission_clock)
        else:
            self._sink.flush()
            self._store.clear()
            log.info(
                "stream %s: complete, %d samples (%d in this run)",
                self.fingerprint[:12],
                total,
                total - before,
            )
            return ReplayResult(published=total - before, total=total, completed=True)

        self._sink.flush()
        self._save(total, last, mission_clock)
        log.info(
            "stream %s: stopped after %d samples (%d in this run), checkpoint saved",
            self.fingerprint[:12],
            total,
            total - before,
        )
        return ReplayResult(published=total - before, total=total, completed=False)

    def _ordered(self, after: Position | None) -> Iterator[Reading]:
        """The stream in (mission_ts, channel rank) order, from just after ``after``."""
        start, end = self._window.start, self._window.end
        if after is not None and (start is None or after.mission_ts > start):
            start = after.mission_ts
        ranks = self._ranks

        def key(reading: Reading) -> tuple[datetime, int]:
            return reading.mission_ts, ranks[reading.channel]

        merged = heapq.merge(*(self._source.readings(c, start, end) for c in ranks), key=key)
        if after is None:
            return merged
        done = (after.mission_ts, ranks[after.channel])
        return dropwhile(lambda reading: key(reading) <= done, merged)

    def _save(self, total: int, last: Position | None, mission_clock: datetime) -> None:
        self._store.save(
            Checkpoint(
                stream=self.fingerprint, published=total, last=last, mission_clock=mission_clock
            )
        )
