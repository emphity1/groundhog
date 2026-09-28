# SPDX-License-Identifier: Apache-2.0
"""The replay without the waiting: the same stream, as fast as it can be computed.

The evaluation harness must score exactly the stream a live run would see: the
same readings, in the same order (ADR 0003), published by the same engine. Only
wall time is simulated. The clock jumps to each sample's due moment instead of
sleeping, and at speed 1 from the window's start every ``wall_ts`` equals the
sample's ``mission_ts``, so the stream is fully deterministic.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from groundhog.ingest import Source
from groundhog.replay.checkpoint import Checkpoint
from groundhog.replay.config import CheckpointSettings, ReplaySettings, Window
from groundhog.replay.engine import ReplayEngine
from groundhog.schema import Sample

__all__ = ["CallbackSink", "InstantClock", "NoCheckpoints", "replay_offline"]


class InstantClock:
    """Wall time that advances only when the engine would sleep, and at once."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def sleep_until(self, deadline: datetime, stop: threading.Event) -> bool:
        if stop.is_set():
            return True
        self._now = max(self._now, deadline)
        return False


class CallbackSink:
    """Hands every published sample to a function."""

    def __init__(self, deliver: Callable[[Sample], None]) -> None:
        self._deliver = deliver

    def write(self, sample: Sample) -> None:
        self._deliver(sample)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class NoCheckpoints:
    """An offline replay always runs to the end: nothing to resume from."""

    def load(self) -> Checkpoint | None:
        return None

    def save(self, checkpoint: Checkpoint) -> None:
        pass

    def clear(self) -> None:
        pass


def replay_offline(
    source: Source, seed: int, window: Window, deliver: Callable[[Sample], None]
) -> int:
    """Publish the stream of ``window`` to ``deliver``; return how many samples."""
    if window.start is None:
        raise ValueError("an offline replay needs a window start: its clock starts there")
    engine = ReplayEngine(
        source,
        CallbackSink(deliver),
        InstantClock(window.start),
        NoCheckpoints(),
        ReplaySettings(speed=1.0, seed=seed, window=window, max_sleep_s=1.0),
        CheckpointSettings(path=Path("unused"), every_samples=1 << 62, min_idle_s=0.0),
    )
    result = engine.run(threading.Event())
    return result.published
