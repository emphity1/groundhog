# SPDX-License-Identifier: Apache-2.0
"""R0 limit checking on streams whose violations are known by construction."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from groundhog.detectors.base import DetectorError
from groundhog.detectors.r0_limits import LimitChecker, LimitsConfig
from groundhog.schema import ChannelKind, DetectorId, Quality, Sample, Score

T0 = datetime(2022, 1, 4, 20, 0, tzinfo=UTC)
WALL = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
LIMITS = {"check": "limits", "low": 0.0, "high": 10.0, "max_rate": None}  # span 10
STATES = {"check": "states", "allowed": [0, 1]}


def limits(
    channels: dict[str, dict[str, Any]],
    *,
    hysteresis: float = 0.0,
    persistence: dict[str, Any] | None = None,
    max_gap_s: float = 60,
) -> LimitsConfig:
    return LimitsConfig.model_validate(
        {
            "mission": "m",
            "provenance": {"made": "by hand, for a test"},
            "defaults": {
                "hysteresis": hysteresis,
                "persistence": persistence or {"samples": 1},
                "max_gap_s": max_gap_s,
            },
            "channels": channels,
        }
    )


def samples(
    channel: str,
    points: Sequence[tuple[float, float]],
    *,
    kind: ChannelKind = ChannelKind.NUMERIC,
    qualities: Sequence[Quality] = (),
) -> list[Sample]:
    """(seconds after T0, value) pairs as samples of one channel."""
    return [
        Sample(
            mission="m",
            channel=channel,
            mission_ts=T0 + timedelta(seconds=t),
            wall_ts=WALL,
            value=v,
            kind=kind,
            quality=qualities[i] if qualities else Quality.OK,
        )
        for i, (t, v) in enumerate(points)
    ]


def every_second(channel: str, values: Sequence[float], **kw: Any) -> list[Sample]:
    return samples(channel, [(float(i), v) for i, v in enumerate(values)], **kw)


def run(detector: LimitChecker, stream: Sequence[Sample]) -> list[Score | None]:
    return [detector.update(s) for s in stream]


def firing(scores: Sequence[Score | None]) -> list[bool]:
    return [s is not None and s.firing for s in scores]


def onsets(scores: Sequence[Score | None]) -> set[datetime]:
    return {s.onset for s in scores if s is not None and s.onset is not None}


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


class TestLimits:
    def test_inside_the_limits_is_a_zero_score_not_silence(self) -> None:
        scores = run(LimitChecker(limits({"A": LIMITS})), every_second("A", [0, 5, 10]))
        assert all(s is not None for s in scores)
        assert [(s.value, s.firing, s.onset) for s in scores if s] == [(0, False, None)] * 3

    def test_the_score_is_how_far_outside_in_units_of_the_limit_span(self) -> None:
        scores = run(LimitChecker(limits({"A": LIMITS})), every_second("A", [12, -5, 10.5]))
        assert [s.value for s in scores if s] == pytest.approx([0.2, 0.5, 0.05])
        assert {s.detector for s in scores if s} == {DetectorId.R0_LIMITS}

    def test_one_noisy_sample_starts_no_alarm(self) -> None:
        r0 = LimitChecker(limits({"A": LIMITS}, persistence={"samples": 3}))
        scores = run(r0, every_second("A", [1, 50, 1, 1, 60, 70, 1]))
        assert not any(firing(scores))
        assert [s.value > 0 for s in scores if s] == [False, True, False, False, True, True, False]

    def test_persistence_in_samples_fires_late_but_dates_the_alarm_from_its_start(self) -> None:
        r0 = LimitChecker(limits({"A": LIMITS}, persistence={"samples": 3}))
        scores = run(r0, every_second("A", [1, 11, 12, 13, 14, 1]))
        assert firing(scores) == [False, False, False, True, True, False]
        assert onsets(scores) == {at(1)}

    def test_persistence_in_mission_seconds(self) -> None:
        r0 = LimitChecker(limits({"A": LIMITS}, persistence={"seconds": 10}))
        stream = samples("A", [(0, 11), (5, 12), (9, 13), (10, 14), (15, 1)])
        scores = run(r0, stream)
        assert firing(scores) == [False, False, False, True, False]
        assert onsets(scores) == {at(0)}

    def test_hysteresis_holds_the_alarm_until_the_value_is_well_back_inside(self) -> None:
        r0 = LimitChecker(limits({"A": LIMITS}, hysteresis=0.1))  # clears at or below 9
        scores = run(r0, every_second("A", [11, 9.5, 9.1, 9.0, 9.5]))
        assert firing(scores) == [True, True, True, False, False]
        assert [s.value for s in scores if s] == [pytest.approx(0.1), 0, 0, 0, 0]

    def test_hysteresis_turns_an_alarm_storm_into_one_alarm(self) -> None:
        flapping = every_second("A", [10.5, 9.8] * 5)
        storm = run(LimitChecker(limits({"A": LIMITS})), flapping)
        calm = run(LimitChecker(limits({"A": LIMITS}, hysteresis=0.05)), flapping)
        assert len(onsets(storm)) == 5
        assert len(onsets(calm)) == 1 and all(firing(calm))

    def test_a_rate_limit_catches_a_jump_inside_the_limits(self) -> None:
        channel = LIMITS | {"max_rate": 1.0}
        scores = run(LimitChecker(limits({"A": channel})), every_second("A", [1, 1.5, 4.0, 4.5]))
        assert firing(scores) == [False, False, True, False]
        assert scores[2] is not None and scores[2].value == pytest.approx(1.5)  # 2.5/s over 1/s

    def test_a_rate_is_never_measured_across_a_gap(self) -> None:
        channel = LIMITS | {"max_rate": 0.05}
        close = run(LimitChecker(limits({"A": channel})), samples("A", [(0, 0), (50, 9)]))
        apart = run(LimitChecker(limits({"A": channel})), samples("A", [(0, 0), (100, 9)]))
        assert firing(close) == [False, True]  # 0.18/s
        assert firing(apart) == [False, False]  # 100 s > max_gap_s: no rate

    def test_a_gap_ends_the_alarm_and_persistence_starts_over(self) -> None:
        r0 = LimitChecker(limits({"A": LIMITS}, persistence={"samples": 2}))
        stream = samples("A", [(0, 11), (1, 11), (2, 11), (200, 11), (201, 11)])
        scores = run(r0, stream)
        assert firing(scores) == [False, True, True, False, True]
        assert onsets(scores) == {at(0), at(200)}

    def test_a_rate_score_inherits_the_worse_quality_of_its_two_samples(self) -> None:
        stream = every_second("A", [1, 2], qualities=[Quality.GAP_FILLED, Quality.OK])
        with_rate = run(LimitChecker(limits({"A": LIMITS | {"max_rate": 5.0}})), stream)
        without = run(LimitChecker(limits({"A": LIMITS})), stream)
        assert [s.quality for s in with_rate if s] == [Quality.GAP_FILLED, Quality.GAP_FILLED]
        assert [s.quality for s in without if s] == [Quality.GAP_FILLED, Quality.OK]

    def test_a_value_that_is_not_a_number_gets_no_opinion(self) -> None:
        scores = run(LimitChecker(limits({"A": LIMITS})), every_second("A", [1, float("nan"), 2]))
        assert [s is None for s in scores] == [False, True, False]

    def test_samples_out_of_order_are_refused(self) -> None:
        r0 = LimitChecker(limits({"A": LIMITS}))
        r0.update(samples("A", [(5, 1)])[0])
        with pytest.raises(DetectorError, match="mission-time order"):
            r0.update(samples("A", [(4, 1)])[0])

    def test_reset_forgets_every_channel(self) -> None:
        r0 = LimitChecker(limits({"A": LIMITS, "B": LIMITS}, persistence={"samples": 2}))
        run(r0, every_second("A", [11]) + every_second("B", [11]))
        r0.reset()
        assert firing(run(r0, samples("A", [(10, 11)]) + samples("B", [(10, 11)]))) == [False] * 2


class TestStates:
    def test_a_code_outside_the_allowed_states_is_a_violation(self) -> None:
        stream = every_second("M", [0, 1, 2, 1], kind=ChannelKind.CATEGORICAL)
        scores = run(LimitChecker(limits({"M": STATES})), stream)
        assert [s.value for s in scores if s] == [0, 0, 1, 0]
        assert firing(scores) == [False, False, True, False]

    def test_persistence_applies_to_states_too(self) -> None:
        stream = every_second("M", [2, 0, 2, 2], kind=ChannelKind.CATEGORICAL)
        channel = STATES | {"persistence": {"samples": 2}}
        assert firing(run(LimitChecker(limits({"M": channel})), stream)) == [
            False,
            False,
            False,
            True,
        ]

    def test_numeric_limits_on_a_categorical_channel_are_refused(self) -> None:
        with pytest.raises(DetectorError, match="numeric limits do not apply"):
            run(LimitChecker(limits({"M": LIMITS})), every_second("M", [1], kind="categorical"))

    def test_allowed_states_on_a_numeric_channel_are_refused(self) -> None:
        with pytest.raises(DetectorError, match="categorical channels only"):
            run(LimitChecker(limits({"A": STATES})), every_second("A", [1]))


class TestNothingIsIgnoredInSilence:
    def test_a_channel_missing_from_the_limits_stops_the_run(self) -> None:
        with pytest.raises(DetectorError, match="has no entry in the limits"):
            run(LimitChecker(limits({"A": LIMITS})), every_second("B", [1]))

    def test_an_unchecked_channel_gets_no_opinion_and_must_say_why(self) -> None:
        unchecked = {"check": "none", "reason": "no limits known yet"}
        stream = every_second("T", [1, 2], kind=ChannelKind.TELECOMMAND)
        assert run(LimitChecker(limits({"T": unchecked})), stream) == [None, None]
        with pytest.raises(ValidationError):
            limits({"T": {"check": "none"}})

    def test_samples_of_another_mission_are_refused(self) -> None:
        sample = every_second("A", [1])[0].model_copy(update={"mission": "other"})
        with pytest.raises(DetectorError, match="not 'other'"):
            LimitChecker(limits({"A": LIMITS})).update(sample)


class TestNoAccessToTheFuture:
    STREAM = samples(
        "A",
        [(0, 1), (1, 11), (2, 12), (3, 9.6), (4, 13), (5, 1), (200, 11), (201, 12), (202, 1)],
    )

    def detector(self) -> LimitChecker:
        return LimitChecker(
            limits({"A": LIMITS | {"max_rate": 4.0}}, hysteresis=0.05, persistence={"samples": 2})
        )

    def test_no_answer_changes_when_the_stream_is_cut_short(self) -> None:
        whole = run(self.detector(), self.STREAM)
        for k in range(len(self.STREAM) + 1):
            assert run(self.detector(), self.STREAM[:k]) == whole[:k]

    @pytest.mark.parametrize("future", [-1e9, 0.0, 1e9])
    def test_no_answer_changes_when_the_future_does(self, future: float) -> None:
        whole = run(self.detector(), self.STREAM)
        for k in range(len(self.STREAM)):
            changed = [
                *self.STREAM[:k],
                *(s.model_copy(update={"value": future}) for s in self.STREAM[k:]),
            ]
            assert run(self.detector(), changed)[:k] == whole[:k]


class TestConfiguration:
    def test_the_hash_follows_every_parameter(self) -> None:
        base = limits({"A": LIMITS})
        assert LimitChecker(base).config_hash == LimitChecker(limits({"A": LIMITS})).config_hash
        for changed in (
            limits({"A": LIMITS | {"high": 11.0}}),
            limits({"A": LIMITS}, hysteresis=0.1),
            limits({"A": LIMITS}, persistence={"samples": 2}),
            limits({"A": LIMITS}, max_gap_s=30),
        ):
            assert LimitChecker(changed).config_hash != LimitChecker(base).config_hash

    @pytest.mark.parametrize(
        "channel",
        [
            LIMITS | {"low": 10.0},
            LIMITS | {"hysteresis": 0.5},
            LIMITS | {"persistence": {"samples": 2, "seconds": 5}},
            LIMITS | {"max_rate": 0},
            {"check": "states", "allowed": []},
            {"check": "sometimes"},
        ],
    )
    def test_refuses_what_cannot_work(self, channel: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            limits({"A": channel})

    def test_a_channel_overrides_the_defaults(self) -> None:
        quick = LIMITS | {"persistence": {"samples": 1}}
        r0 = LimitChecker(limits({"A": quick, "B": LIMITS}, persistence={"samples": 3}))
        scores = run(r0, every_second("A", [11]) + every_second("B", [11]))
        assert firing(scores) == [True, False]
