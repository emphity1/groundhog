# SPDX-License-Identifier: Apache-2.0
"""The replay engine's parts: the system clock, the checkpoint file, the configuration."""

from __future__ import annotations

import copy
import errno
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from groundhog.replay.checkpoint import (
    Checkpoint,
    CheckpointError,
    JsonFileCheckpointStore,
    Position,
)
from groundhog.replay.clock import SystemClock
from groundhog.replay.config import ReplayConfig, load_config
from groundhog.replay.sinks import JsonlSink, SinkError
from groundhog.schema import Reading
from synthetic_opssat import FIRST

SHIPPED = Path(__file__).resolve().parents[1] / "configs" / "replay" / "opssat.yaml"


class TestSystemClock:
    def test_now_is_utc_and_never_goes_backwards(self) -> None:
        clock = SystemClock(max_sleep_s=0.25)
        readings = [clock.now() for _ in range(1000)]
        assert readings[0].tzinfo is UTC
        assert readings == sorted(readings)

    def test_returns_at_once_when_stopped_or_already_due(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(time, "sleep", lambda s: pytest.fail(f"slept {s} s"))
        clock = SystemClock(max_sleep_s=0.25)
        stopped = threading.Event()
        stopped.set()
        assert clock.sleep_until(clock.now() + timedelta(days=1), stopped) is True
        assert clock.sleep_until(clock.now() - timedelta(seconds=1), threading.Event()) is False

    def test_long_waits_are_cut_into_naps_so_a_stop_is_noticed_quickly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elapsed = [0.0]
        naps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            naps.append(seconds)
            elapsed[0] += seconds

        monkeypatch.setattr(time, "perf_counter", lambda: elapsed[0])
        monkeypatch.setattr(time, "sleep", fake_sleep)
        clock = SystemClock(max_sleep_s=0.25)
        assert clock.sleep_until(clock.now() + timedelta(seconds=1.1), threading.Event()) is False
        assert naps[:4] == [0.25] * 4 and sum(naps) == pytest.approx(1.1)

    def test_refuses_a_non_positive_nap(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            SystemClock(max_sleep_s=0)


class TestJsonlSink:
    class Unwritable:
        def write(self, data: bytes) -> int:
            raise OSError(errno.EINVAL, "Invalid argument")  # a closed pipe, on Windows

        def flush(self) -> None:
            raise BrokenPipeError(errno.EPIPE, "Broken pipe")  # a closed pipe, on POSIX

    def test_an_output_that_cannot_be_written_is_a_sink_error(self) -> None:
        sample = Reading(mission="m", channel="c", mission_ts=FIRST, value=1.0).publish(FIRST)
        sink = JsonlSink(self.Unwritable())  # type: ignore[arg-type]
        with pytest.raises(SinkError, match="cannot write the stream"):
            sink.write(sample)
        with pytest.raises(SinkError, match="Broken pipe"):
            sink.close()


class TestJsonFileCheckpointStore:
    def checkpoint(self) -> Checkpoint:
        return Checkpoint(
            stream="f" * 64,
            published=3,
            last=Position(mission_ts=FIRST, channel="CH_A"),
            mission_clock=FIRST,
        )

    def test_save_load_clear(self, tmp_path: Path) -> None:
        store = JsonFileCheckpointStore(tmp_path / "state" / "replay.json")
        assert store.load() is None
        store.save(self.checkpoint())
        assert store.load() == self.checkpoint()
        assert [p.name for p in (tmp_path / "state").iterdir()] == ["replay.json"]  # no temp left
        store.clear()
        assert store.load() is None
        store.clear()  # nothing to clear is fine

    def test_a_save_replaces_the_previous_one(self, tmp_path: Path) -> None:
        store = JsonFileCheckpointStore(tmp_path / "replay.json")
        store.save(self.checkpoint())
        later = self.checkpoint().model_copy(update={"published": 4})
        store.save(later)
        assert store.load() == later

    def test_refuses_a_corrupt_file(self, tmp_path: Path) -> None:
        path = tmp_path / "replay.json"
        path.write_text("{", encoding="utf-8")
        with pytest.raises(CheckpointError, match="not a valid checkpoint"):
            JsonFileCheckpointStore(path).load()

    def test_timestamps_must_carry_a_time_zone(self) -> None:
        with pytest.raises(ValidationError):
            Checkpoint(stream="s", published=0, last=None, mission_clock=datetime(2022, 1, 4))


def config_keys(tree: dict[str, Any], path: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    """Every key of the configuration, except the channel ids, which are data."""
    for key, value in tree.items():
        here = (*path, key)
        if here[:2] != ("source", "channels") or len(here) != 3:
            yield here
        if isinstance(value, dict):
            yield from config_keys(value, here)


def without(tree: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    pruned = copy.deepcopy(tree)
    node = pruned
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    return pruned


class TestConfig:
    def test_the_shipped_opssat_config_is_valid(self) -> None:
        config = load_config(SHIPPED)
        assert config.replay.speed == 1000
        assert len(config.source.channels) == 9

    def test_every_key_is_required_no_default_hides_in_code(self) -> None:
        tree = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
        keys = list(config_keys(tree))
        assert ("replay", "window", "start") in keys and ("checkpoint", "min_idle_s") in keys
        for path in keys:
            with pytest.raises(ValidationError):
                ReplayConfig.model_validate(without(tree, path))

    @pytest.mark.parametrize(
        ("path", "value"),
        [
            (("replay", "speed"), 0),
            (("replay", "max_sleep_s"), 0),
            (("checkpoint", "every_samples"), 0),
            (
                ("replay", "window"),
                {"start": "2022-01-05T00:00:00Z", "end": "2022-01-04T00:00:00Z"},
            ),
            (("replay", "window"), {"start": "2022-01-04T00:00:00", "end": None}),
            (("replay", "surprise"), 1),
            (("sink", "type"), "kafka"),
        ],
    )
    def test_refuses_bad_values(self, path: tuple[str, ...], value: object) -> None:
        tree = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
        tree[path[0]][path[1]] = value
        with pytest.raises(ValidationError):
            ReplayConfig.model_validate(tree)

    def test_window_bounds_are_normalised_to_utc(self) -> None:
        tree = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
        tree["replay"]["window"] = {"start": "2022-01-04T22:00:00+02:00", "end": None}
        start = ReplayConfig.model_validate(tree).replay.window.start
        assert start == FIRST and start is not None and start.tzinfo is UTC
