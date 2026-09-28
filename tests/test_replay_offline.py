# SPDX-License-Identifier: Apache-2.0
"""The offline replay publishes exactly the live stream, without waiting for it."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from groundhog.ingest.opssat import OpssatSource, OpssatSourceConfig
from groundhog.replay.config import Window, load_config
from groundhog.replay.engine import ReplayEngine
from groundhog.replay.offline import InstantClock, replay_offline
from groundhog.schema import Sample
from replay_doubles import FakeClock, MemorySink, MemoryStore, write_replay_config
from synthetic_opssat import FIRST, TOTAL

WINDOW = Window(start=FIRST, end=None)


def test_the_same_samples_in_the_same_order_as_a_live_replay(
    opssat_config: OpssatSourceConfig, tmp_path: Path
) -> None:
    config = load_config(write_replay_config(tmp_path, opssat_config, seed=7))
    settings = config.replay.model_copy(update={"window": WINDOW})
    live = MemorySink()
    engine = ReplayEngine(
        OpssatSource(opssat_config), live, FakeClock(), MemoryStore(), settings, config.checkpoint
    )
    engine.run(threading.Event())

    offline: list[Sample] = []
    count = replay_offline(OpssatSource(opssat_config), 7, WINDOW, offline.append)
    assert count == len(offline) == TOTAL
    assert [s.model_dump(exclude={"wall_ts"}) for s in offline] == [
        s.model_dump(exclude={"wall_ts"}) for s in live.samples
    ]


def test_wall_time_equals_mission_time_and_nothing_waits(
    opssat_config: OpssatSourceConfig,
) -> None:
    samples: list[Sample] = []
    started = time.perf_counter()
    replay_offline(OpssatSource(opssat_config), 0, WINDOW, samples.append)
    assert time.perf_counter() - started < 5  # the data spans three hours
    assert samples and all(s.wall_ts == s.mission_ts for s in samples)


def test_an_offline_replay_needs_a_start(opssat_config: OpssatSourceConfig) -> None:
    with pytest.raises(ValueError, match="window start"):
        replay_offline(OpssatSource(opssat_config), 0, Window(start=None, end=None), print)


def test_the_clock_jumps_and_honours_a_stop() -> None:
    t0 = datetime(2022, 1, 4, tzinfo=UTC)
    later = datetime(2022, 1, 5, tzinfo=UTC)
    clock = InstantClock(t0)
    assert clock.sleep_until(later, threading.Event()) is False
    assert clock.now() == later
    assert clock.sleep_until(t0, threading.Event()) is False
    assert clock.now() == later
    stopped = threading.Event()
    stopped.set()
    assert clock.sleep_until(datetime(2022, 1, 6, tzinfo=UTC), stopped) is True
