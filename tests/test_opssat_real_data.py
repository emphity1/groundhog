# SPDX-License-Identifier: Apache-2.0
"""The shipped configuration against the real OPSSAT-AD files.

Skipped when the dataset has not been fetched (``make data-fetch DS=opssat``), as in
CI: datasets never enter the repository.
"""

from __future__ import annotations

import csv
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from groundhog.ingest.opssat import OpssatSource
from groundhog.replay.config import ReplayConfig, Window, load_config
from groundhog.replay.engine import ReplayEngine
from replay_doubles import WALL0, FakeClock, MemorySink, MemoryStore

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "raw" / "opssat" / "segments.csv"

pytestmark = pytest.mark.skipif(not DATA.exists(), reason="OPSSAT-AD not fetched")


@pytest.fixture(scope="module")
def config() -> ReplayConfig:
    shipped = load_config(REPO / "configs" / "replay" / "opssat.yaml")
    return shipped.model_copy(
        update={"source": shipped.source.model_copy(update={"path": REPO / shipped.source.path})}
    )


@pytest.fixture(scope="module")
def source(config: ReplayConfig) -> OpssatSource:
    return OpssatSource(config.source)  # refuses undeclared or missing channels


@pytest.fixture(scope="module")
def rows() -> list[tuple[str, datetime]]:
    with DATA.open(newline="", encoding="utf-8") as fh:
        return [(r["channel"], datetime.fromisoformat(r["timestamp"])) for r in csv.DictReader(fh)]


def test_every_row_becomes_exactly_one_reading(
    source: OpssatSource, rows: list[tuple[str, datetime]]
) -> None:
    per_channel = {c: len(list(source.readings(c, None, None))) for c in source.channels()}
    assert sum(per_channel.values()) == len(rows) == 303_493
    assert len(per_channel) == 9


def test_an_hour_of_the_june_night_replays_faithfully(
    config: ReplayConfig, source: OpssatSource, rows: list[tuple[str, datetime]]
) -> None:
    start = datetime(2022, 6, 1, 23, 39, tzinfo=UTC)
    end = start + timedelta(hours=1)
    settings = config.replay.model_copy(update={"window": Window(start=start, end=end)})
    stop = threading.Event()
    sink = MemorySink()
    clock = FakeClock()
    result = ReplayEngine(source, sink, clock, MemoryStore(), settings, config.checkpoint).run(stop)

    assert result.completed
    expected = sorted((t, c) for c, t in rows if start <= t < end)
    assert sorted((s.mission_ts, s.channel) for s in sink.samples) == expected
    for channel in source.channels():
        stamps = [s.mission_ts for s in sink.samples if s.channel == channel]
        assert stamps == sorted(stamps)
    for sample in sink.samples:
        assert sample.wall_ts - WALL0 == (sample.mission_ts - start) / config.replay.speed
