# SPDX-License-Identifier: Apache-2.0
"""The replay engine against the M1 contract.

Every test runs on a clock that jumps instead of waiting, and ``time.sleep`` is
booby-trapped: hours of mission time are replayed without spending real time.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

from groundhog.ingest.opssat import OpssatSource, OpssatSourceConfig
from groundhog.replay.checkpoint import Position
from groundhog.replay.config import CheckpointSettings, ReplaySettings, Window
from groundhog.replay.engine import (
    CheckpointMismatchError,
    ReplayEngine,
    ReplayResult,
    channel_ranks,
)
from groundhog.replay.sinks import JsonlSink
from groundhog.schema import Sample
from replay_doubles import WALL0, FakeClock, MemorySink, MemoryStore
from synthetic_opssat import FIRST, GAP, INNER_GAP, TOTAL, expected_by_channel

LAST = GAP[1] + timedelta(seconds=3)  # CH_B, 23:00:03
TIE3 = FIRST + timedelta(seconds=2)  # the instant all three channels share


@pytest.fixture(autouse=True)
def no_real_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(seconds: float) -> None:
        raise AssertionError(f"a test tried to sleep {seconds} s of real time")

    monkeypatch.setattr(time, "sleep", refuse)


@dataclass
class Run:
    result: ReplayResult
    samples: list[Sample]
    clock: FakeClock


def replay(
    config: OpssatSourceConfig,
    store: MemoryStore | None = None,
    *,
    clock: FakeClock | None = None,
    stop_after: int | None = None,
    speed: float = 1000,
    seed: int = 0,
    start: datetime | None = None,
    end: datetime | None = None,
    every: int = 5,
    min_idle_s: float = 1.0,
) -> Run:
    stop = threading.Event()
    sink = MemorySink(stop, stop_after)
    store = store if store is not None else MemoryStore()
    store.watched = sink
    clock = clock if clock is not None else FakeClock()
    engine = ReplayEngine(
        OpssatSource(config),
        sink,
        clock,
        store,
        ReplaySettings(speed=speed, seed=seed, window=Window(start=start, end=end), max_sleep_s=1),
        CheckpointSettings(path=Path("unused"), every_samples=every, min_idle_s=min_idle_s),
    )
    return Run(engine.run(stop), sink.samples, clock)


def content(samples: list[Sample]) -> list[tuple[str, datetime, float]]:
    """What a sample says, without when it was published."""
    return [(s.channel, s.mission_ts, s.value) for s in samples]


def jsonl(samples: list[Sample]) -> bytes:
    buffer = BytesIO()
    sink = JsonlSink(buffer)
    for sample in samples:
        sink.write(sample)
    sink.close()
    return buffer.getvalue()


class TestDeterminism:
    """Scope of the claim (ADR 0003): same data version, same window, same seed."""

    def test_same_data_window_and_seed_give_the_same_stream(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        # Byte-identical, wall_ts included, because both runs share the same clock.
        first = replay(opssat_config, seed=7, start=FIRST, end=LAST)
        second = replay(opssat_config, seed=7, start=FIRST, end=LAST)
        assert len(first.samples) == TOTAL - 1  # the window excludes LAST
        assert jsonl(first.samples) == jsonl(second.samples)

    def test_speed_is_not_part_of_the_stream_identity(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        slow, fast = replay(opssat_config, speed=1), replay(opssat_config, speed=1000)
        assert content(slow.samples) == content(fast.samples)
        assert [s.wall_ts for s in slow.samples] != [s.wall_ts for s in fast.samples]

    def test_every_reading_is_published_exactly_once(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        source = OpssatSource(opssat_config)
        read = [r for c in source.channels() for r in source.readings(c, None, None)]
        published = replay(opssat_config).samples
        key = lambda d: (d["channel"], d["mission_ts"])  # noqa: E731
        assert sorted((s.model_dump(exclude={"wall_ts"}) for s in published), key=key) == sorted(
            (r.model_dump() for r in read), key=key
        )

    def test_mission_time_never_goes_backwards(self, opssat_config: OpssatSourceConfig) -> None:
        stamps = [s.mission_ts for s in replay(opssat_config).samples]
        assert stamps == sorted(stamps)

    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 42])
    def test_simultaneous_samples_follow_the_seeded_channel_rank(
        self, opssat_config: OpssatSourceConfig, seed: int
    ) -> None:
        rank = channel_ranks(["CH_A", "CH_B", "CH_C"], seed)
        keys = [(s.mission_ts, rank[s.channel]) for s in replay(opssat_config, seed=seed).samples]
        assert keys == sorted(keys)

    def test_the_seed_changes_the_interleaving_and_nothing_else(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        tie_orders = set()
        for seed in range(8):
            samples = replay(opssat_config, seed=seed).samples
            for channel, expected in expected_by_channel().items():
                assert [
                    (s.mission_ts, s.value) for s in samples if s.channel == channel
                ] == expected
            tie_orders.add(tuple(s.channel for s in samples if s.mission_ts == TIE3))
        assert len(tie_orders) > 1

    def test_channel_ranks_are_a_pure_function_of_seed_and_channels(self) -> None:
        assert channel_ranks(["X", "Y", "Z"], 5) == channel_ranks(["Z", "X", "Y"], 5)
        assert sorted(channel_ranks(["X", "Y", "Z"], 5).values()) == [0, 1, 2]


class TestPerChannelOrder:
    def test_each_channel_keeps_mission_time_order_whatever_the_file_order(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        samples = replay(opssat_config).samples
        for channel, expected in expected_by_channel().items():
            assert [(s.mission_ts, s.value) for s in samples if s.channel == channel] == expected


class TestGapFidelity:
    def test_a_gap_in_the_source_is_silence_in_the_stream(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        samples = replay(opssat_config, speed=1000).samples
        i = max(i for i, s in enumerate(samples) if s.mission_ts <= GAP[0])
        before, after = samples[i], samples[i + 1]
        assert (before.mission_ts, after.mission_ts) == GAP  # nothing published inside it
        assert after.wall_ts - before.wall_ts == (GAP[1] - GAP[0]) / 1000  # 10.655 s

    def test_the_engine_waits_through_the_gap_rather_than_skipping_it(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        waits = [until - since for since, until in replay(opssat_config, speed=1000).clock.waits]
        assert (GAP[1] - GAP[0]) / 1000 in waits

    def test_a_gap_inside_a_segment_is_kept_too(self, opssat_config: OpssatSourceConfig) -> None:
        samples = replay(opssat_config, speed=1000).samples
        i = next(i for i, s in enumerate(samples) if s.mission_ts == INNER_GAP[0])
        before, after = samples[i], samples[i + 1]
        assert (before.mission_ts, after.mission_ts) == INNER_GAP
        assert after.wall_ts - before.wall_ts == timedelta(seconds=122) / 1000

    def test_the_silence_before_the_first_sample_belongs_to_the_window(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        run = replay(opssat_config, start=FIRST - timedelta(hours=1), speed=1000)
        assert run.samples[0].wall_ts - WALL0 == timedelta(hours=1) / 1000

    def test_the_window_bounds_the_stream(self, opssat_config: OpssatSourceConfig) -> None:
        start, end = TIE3, GAP[1] + timedelta(seconds=1)
        samples = replay(opssat_config, start=start, end=end).samples
        assert samples and all(start <= s.mission_ts < end for s in samples)
        assert len(samples) == sum(
            start <= t < end for points in expected_by_channel().values() for t, _ in points
        )

    def test_a_window_without_data_completes_at_once(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        store = MemoryStore()
        run = replay(opssat_config, store, start=GAP[0] + timedelta(minutes=1), end=GAP[1])
        assert (run.result.completed, run.samples, store.checkpoint) == (True, [], None)


class TestSpeed:
    @pytest.mark.parametrize("speed", [0.5, 1, 60, 1000, 1e9])
    def test_wall_time_is_mission_time_divided_by_speed(
        self, opssat_config: OpssatSourceConfig, speed: float
    ) -> None:
        began = time.perf_counter()
        run = replay(opssat_config, speed=speed)
        for sample in run.samples:
            assert sample.wall_ts - WALL0 == (sample.mission_ts - FIRST) / speed
        assert run.clock.t - WALL0 == (LAST - FIRST) / speed  # up to 6 h of wall time at 0.5x
        assert time.perf_counter() - began < 5  # ... spent in well under a second

    def test_a_late_sample_goes_out_at_once_and_nothing_is_dropped(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        class SlowClock(FakeClock):
            def now(self) -> datetime:  # every look at the clock costs a second
                self.t += timedelta(seconds=1)
                return self.t

        run = replay(opssat_config, clock=SlowClock(), speed=1e6)
        assert content(run.samples) == content(replay(opssat_config).samples)
        assert run.clock.waits == []  # always behind schedule, so never waiting
        walls = [s.wall_ts for s in run.samples]
        assert walls == sorted(walls)


class TestResume:
    @pytest.mark.parametrize("k", range(TOTAL))
    def test_after_a_stop_at_any_point_the_resumed_stream_repeats_and_skips_nothing(
        self, opssat_config: OpssatSourceConfig, k: int
    ) -> None:
        store = MemoryStore()
        first = replay(opssat_config, store, stop_after=k)
        assert not first.result.completed
        assert store.checkpoint is not None
        assert store.checkpoint.published == k == len(first.samples)
        assert store.checkpoint.last == (
            Position(mission_ts=first.samples[-1].mission_ts, channel=first.samples[-1].channel)
            if k
            else None
        )

        wall = WALL0 + timedelta(days=1)
        second = replay(opssat_config, store, clock=FakeClock(wall))
        assert second.result.completed and store.checkpoint is None
        assert second.result.total == TOTAL
        assert content(first.samples + second.samples) == content(replay(opssat_config).samples)

        # The resumed stream keeps the pace: the next sample waits exactly the
        # mission time elapsed since the last one published.
        resumed_from = first.samples[-1].mission_ts if k else FIRST
        assert (
            second.samples[0].wall_ts - wall == (second.samples[0].mission_ts - resumed_from) / 1000
        )

    def test_a_stop_inside_a_gap_resumes_with_only_the_rest_of_it(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        gap = GAP[1] - GAP[0]
        gap_opens = WALL0 + (GAP[0] - FIRST) / 1000
        store = MemoryStore()
        first = replay(opssat_config, store, clock=FakeClock(stop_at=gap_opens + gap / 1000 / 4))
        assert first.samples[-1].mission_ts == GAP[0]
        assert store.checkpoint is not None
        assert store.checkpoint.mission_clock == GAP[0] + gap / 4

        wall = WALL0 + timedelta(days=1)
        second = replay(opssat_config, store, clock=FakeClock(wall))
        assert second.samples[0].mission_ts == GAP[1]
        assert second.samples[0].wall_ts - wall == gap * 3 / 4 / 1000
        assert content(first.samples + second.samples) == content(replay(opssat_config).samples)

    def test_a_stop_after_the_last_sample_still_completes(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        store = MemoryStore()
        run = replay(opssat_config, store, stop_after=TOTAL)
        assert run.result.completed and len(run.samples) == TOTAL and store.checkpoint is None

    def test_a_new_speed_resumes_the_same_stream(self, opssat_config: OpssatSourceConfig) -> None:
        store = MemoryStore()
        first = replay(opssat_config, store, stop_after=10)
        second = replay(opssat_config, store, speed=60)
        assert content(first.samples + second.samples) == content(replay(opssat_config).samples)

    @pytest.mark.parametrize("change", ["seed", "window", "data"])
    def test_a_checkpoint_of_another_stream_is_refused(
        self, opssat_config: OpssatSourceConfig, tmp_path: Path, change: str
    ) -> None:
        store = MemoryStore()
        replay(opssat_config, store, stop_after=3)
        config, seed, end = opssat_config, 0, None
        if change == "seed":
            seed = 1
        elif change == "window":
            end = GAP[1]
        else:
            edited = tmp_path / "edited.csv"
            text = opssat_config.path.read_text(encoding="utf-8")
            edited.write_text(text.replace(",1.02,", ",1.92,"), encoding="utf-8", newline="\n")
            config = opssat_config.model_copy(update={"path": edited})
        with pytest.raises(CheckpointMismatchError):
            replay(config, store, seed=seed, end=end)


class TestCheckpoints:
    def test_saved_only_once_the_sink_has_flushed_everything_it_counts(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        store = MemoryStore()
        replay(opssat_config, store, every=3, min_idle_s=0)  # a save before every wait, too
        assert len(store.history) > TOTAL // 3
        for checkpoint, (flushed, written) in zip(store.history, store.sink_at_save, strict=True):
            assert flushed == written == checkpoint.published

    def test_every_n_samples_and_before_each_long_wait(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        store = MemoryStore()
        replay(opssat_config, store, every=5, min_idle_s=1.0)
        assert [c.published for c in store.history] == [5, 10, 15, 16, 20]
        at_gap = store.history[3]  # the only wait of a second or more: the gap, 10.655 s
        assert at_gap.last is not None and at_gap.last.mission_ts == GAP[0]

    def test_a_completed_replay_leaves_none_and_runs_again_identically(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        store = MemoryStore()
        first = replay(opssat_config, store)
        assert first.result.completed and store.checkpoint is None
        assert jsonl(replay(opssat_config, store).samples) == jsonl(first.samples)


class TestJsonl:
    def test_one_sample_per_lf_terminated_line(self, opssat_config: OpssatSourceConfig) -> None:
        samples = replay(opssat_config).samples
        data = jsonl(samples)
        assert b"\r" not in data and data.endswith(b"\n")
        lines = data.decode().splitlines()
        assert [Sample.model_validate_json(line) for line in lines] == samples
