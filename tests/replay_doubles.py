# SPDX-License-Identifier: Apache-2.0
"""Test doubles for the replay engine's injected dependencies."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from groundhog.replay.checkpoint import Checkpoint
from groundhog.schema import Sample

WALL0 = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


class FakeClock:
    """Jumps to each deadline instead of waiting.

    ``stop_at`` simulates a stop request (Ctrl+C, SIGTERM) arriving at that wall
    time: the wait that spans it ends there, with the stop flag set.
    """

    def __init__(self, start: datetime = WALL0, stop_at: datetime | None = None) -> None:
        self.t = start
        self.stop_at = stop_at
        self.waits: list[tuple[datetime, datetime]] = []  # (from, until)

    def now(self) -> datetime:
        return self.t

    def sleep_until(self, deadline: datetime, stop: threading.Event) -> bool:
        self.waits.append((self.t, deadline))
        if self.stop_at is not None and self.stop_at <= deadline:
            self.t = max(self.t, self.stop_at)
            self.stop_at = None
            stop.set()
            return True
        self.t = max(self.t, deadline)
        return False


class MemorySink:
    """Keeps what it is given and what it has flushed.

    ``stop_after`` simulates a stop request arriving right after that many samples;
    zero means before the first one.
    """

    def __init__(self, stop: threading.Event | None = None, stop_after: int | None = None) -> None:
        self.samples: list[Sample] = []
        self.flushed = 0
        self._stop = stop
        self._stop_after = stop_after
        if stop is not None and stop_after == 0:
            stop.set()

    def write(self, sample: Sample) -> None:
        self.samples.append(sample)
        if self._stop is not None and len(self.samples) == self._stop_after:
            self._stop.set()

    def flush(self) -> None:
        self.flushed = len(self.samples)

    def close(self) -> None:
        self.flush()


class MemoryStore:
    """A checkpoint store in memory that records every save, and what the watched
    sink had written and flushed at that moment."""

    def __init__(self) -> None:
        self.checkpoint: Checkpoint | None = None
        self.history: list[Checkpoint] = []
        self.sink_at_save: list[tuple[int, int]] = []  # (flushed, written)
        self.watched: MemorySink | None = None

    def load(self) -> Checkpoint | None:
        return self.checkpoint

    def save(self, checkpoint: Checkpoint) -> None:
        self.checkpoint = checkpoint
        self.history.append(checkpoint)
        if self.watched is not None:
            self.sink_at_save.append((self.watched.flushed, len(self.watched.samples)))

    def clear(self) -> None:
        self.checkpoint = None
