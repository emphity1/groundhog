# SPDX-License-Identifier: Apache-2.0
"""The data contract is the one thing every component depends on. Pin it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from groundhog.schema import (
    ChannelContribution,
    ChannelKind,
    Detection,
    DetectorId,
    Event,
    Quality,
    Reading,
    Sample,
)

T0 = datetime(2026, 3, 1, 4, 12, tzinfo=UTC)


def make_sample(**overrides: object) -> Sample:
    defaults: dict[str, object] = {
        "mission": "esa_adb/mission1",
        "channel": "TM_EPS_BATT_V",
        "mission_ts": T0,
        "wall_ts": T0 + timedelta(seconds=1),
        "value": 27.9,
        "kind": ChannelKind.NUMERIC,
        "unit": "V",
    }
    return Sample(**(defaults | overrides))  # type: ignore[arg-type]


def make_reading(**overrides: object) -> Reading:
    defaults: dict[str, object] = {
        "mission": "esa_adb/mission1",
        "channel": "TM_EPS_BATT_V",
        "mission_ts": T0,
        "value": 27.9,
        "kind": ChannelKind.NUMERIC,
        "unit": "V",
    }
    return Reading(**(defaults | overrides))  # type: ignore[arg-type]


def make_event(**overrides: object) -> Event:
    defaults: dict[str, object] = {
        "id": "ANO-014",
        "mission": "esa_adb/mission1",
        "t_start": T0,
        "channels": (ChannelContribution(channel="TM_EPS_BATT_V", weight=0.61),),
        "detections": (
            Detection(
                detector=DetectorId.R2_PREDICTIVE,
                model_version="lstm-2026.03.1",
                score=4.2,
                fired_at=T0 + timedelta(minutes=52),
                delay_s=3120.0,
            ),
        ),
        "created_at": T0 + timedelta(minutes=52, seconds=1),
    }
    return Event(**(defaults | overrides))  # type: ignore[arg-type]


class TestSample:
    def test_roundtrip(self) -> None:
        s = make_sample()
        assert Sample.model_validate_json(s.model_dump_json()) == s

    def test_is_frozen(self) -> None:
        s = make_sample()
        with pytest.raises(ValidationError):
            s.value = 1.0  # type: ignore[misc]

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            make_sample(mission_ts=datetime(2026, 3, 1, 4, 12))

    def test_non_utc_timestamp_is_converted(self) -> None:
        from datetime import timezone

        rome = timezone(timedelta(hours=2))
        s = make_sample(mission_ts=datetime(2026, 3, 1, 6, 12, tzinfo=rome))
        assert s.mission_ts == T0
        assert s.mission_ts.tzinfo == UTC

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_sample(operator="dmytro")

    def test_key_is_mission_and_channel(self) -> None:
        assert make_sample().key == "esa_adb/mission1/TM_EPS_BATT_V"

    def test_quality_defaults_to_ok(self) -> None:
        assert make_sample().quality is Quality.OK


class TestReading:
    def test_is_exactly_a_sample_without_wall_ts(self) -> None:
        # Two types share these fields; they must not drift apart.
        expected = {name: f for name, f in Sample.model_fields.items() if name != "wall_ts"}
        assert Reading.model_fields.keys() == expected.keys()
        for name, field in Reading.model_fields.items():
            other = expected[name]
            assert field.annotation == other.annotation, name
            assert field.default == other.default, name
            assert field.description == other.description, name
            assert field.metadata == other.metadata, name

    def test_publish_adds_wall_ts_and_nothing_else(self) -> None:
        reading = make_reading(quality=Quality.SUSPECT)
        wall = T0 + timedelta(hours=1)
        sample = reading.publish(wall)
        assert isinstance(sample, Sample)
        assert sample.wall_ts == wall
        assert sample.model_dump(exclude={"wall_ts"}) == reading.model_dump()

    def test_publish_rejects_naive_wall_ts(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            make_reading().publish(datetime(2026, 3, 1, 5, 0))

    def test_naive_mission_ts_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            make_reading(mission_ts=datetime(2026, 3, 1, 4, 12))

    def test_has_no_wall_ts(self) -> None:
        with pytest.raises(ValidationError):
            make_reading(wall_ts=T0)

    def test_is_frozen(self) -> None:
        with pytest.raises(ValidationError):
            make_reading().value = 1.0  # type: ignore[misc]


class TestEvent:
    def test_roundtrip(self) -> None:
        e = make_event()
        assert Event.model_validate_json(e.model_dump_json()) == e

    def test_open_event_has_no_duration(self) -> None:
        assert make_event().duration_s is None

    def test_duration_is_mission_time(self) -> None:
        e = make_event(t_end=T0 + timedelta(hours=6))
        assert e.duration_s == 21600.0

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError, match="t_end must not precede"):
            make_event(t_end=T0 - timedelta(seconds=1))

    def test_contributions_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not exceed"):
            make_event(
                channels=(
                    ChannelContribution(channel="A", weight=0.7),
                    ChannelContribution(channel="B", weight=0.5),
                )
            )

    def test_first_detection_owns_the_delay(self) -> None:
        early = Detection(
            detector=DetectorId.R1_STATS,
            score=3.0,
            fired_at=T0 + timedelta(minutes=10),
            delay_s=600.0,
        )
        late = Detection(
            detector=DetectorId.R2_PREDICTIVE,
            model_version="lstm-2026.03.1",
            score=4.2,
            fired_at=T0 + timedelta(minutes=52),
            delay_s=3120.0,
        )
        e = make_event(detections=(late, early))
        assert e.first_detection is early
        assert e.fired_by(DetectorId.R2_PREDICTIVE) is late
        assert e.fired_by(DetectorId.R0_LIMITS) is None

    def test_event_needs_at_least_one_detection(self) -> None:
        with pytest.raises(ValidationError):
            make_event(detections=())
