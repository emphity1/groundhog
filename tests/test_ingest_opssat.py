# SPDX-License-Identifier: Apache-2.0
"""The OPSSAT-AD adapter: what it reads, what it refuses, and what it never lets through."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from groundhog.ingest import IngestError
from groundhog.ingest.opssat import OpssatSource, OpssatSourceConfig
from groundhog.schema import Quality, Reading
from synthetic_opssat import CHANNELS, SEGMENTS, Segment, expected_by_channel, write_segments


def config_for(path: Path, **overrides: object) -> OpssatSourceConfig:
    fields: dict[str, object] = {
        "type": "opssat",
        "path": path,
        "mission": "opssat-test",
        "channels": CHANNELS,
    }
    return OpssatSourceConfig.model_validate(fields | overrides)


def all_readings(source: OpssatSource) -> list[Reading]:
    return [r for channel in source.channels() for r in source.readings(channel, None, None)]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


class TestReadings:
    def test_every_channel_in_mission_time_order_whatever_the_file_order(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        source = OpssatSource(opssat_config)
        expected = expected_by_channel()
        assert source.channels() == tuple(expected)
        for channel, points in expected.items():
            got = [(r.mission_ts, r.value) for r in source.readings(channel, None, None)]
            assert got == points

    def test_kind_unit_and_mission_come_from_the_configuration(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        for reading in all_readings(OpssatSource(opssat_config)):
            spec = CHANNELS[reading.channel]
            assert (reading.mission, reading.kind, reading.unit) == (
                "opssat-test",
                spec.kind,
                spec.unit,
            )

    def test_quality_is_ok_everywhere(self, opssat_config: OpssatSourceConfig) -> None:
        assert {r.quality for r in all_readings(OpssatSource(opssat_config))} == {Quality.OK}

    def test_timestamps_are_utc(self, opssat_config: OpssatSourceConfig) -> None:
        assert {r.mission_ts.tzinfo for r in all_readings(OpssatSource(opssat_config))} == {UTC}

    def test_window_includes_start_and_excludes_end(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        source = OpssatSource(opssat_config)
        window = source.readings("CH_A", utc(2022, 1, 4, 20, 0, 1), utc(2022, 1, 4, 20, 0, 3))
        assert [r.mission_ts.second for r in window] == [1, 2]
        assert list(source.readings("CH_A", utc(2022, 1, 5), None)) == []

    def test_labels_never_reach_a_reading(self, tmp_path: Path) -> None:
        relabelled = tuple(
            replace(s, anomaly=1 - s.anomaly, id=s.id + 100, train=1 - s.train, label="normal")
            for s in SEGMENTS
        )
        original = all_readings(OpssatSource(config_for(write_segments(tmp_path / "a.csv"))))
        changed = all_readings(
            OpssatSource(config_for(write_segments(tmp_path / "b.csv", relabelled)))
        )
        assert original == changed
        assert {"anomaly", "label", "segment", "train"}.isdisjoint(Reading.model_fields)


class TestRefusals:
    def test_channel_missing_from_the_configuration(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        declared = {k: v for k, v in CHANNELS.items() if k != "CH_C"}
        with pytest.raises(IngestError, match="without a declared kind and unit: CH_C"):
            OpssatSource(config_for(opssat_config.path, channels=declared))

    def test_declared_channel_missing_from_the_data(
        self, opssat_config: OpssatSourceConfig
    ) -> None:
        declared = CHANNELS | {"CH_Z": CHANNELS["CH_A"]}
        with pytest.raises(IngestError, match=r"declared channels absent .*: CH_Z"):
            OpssatSource(config_for(opssat_config.path, channels=declared))

    def test_two_samples_at_the_same_time_on_one_channel(self, tmp_path: Path) -> None:
        clash = Segment("CH_A", 1, 0, 99, 1, (("2022-01-04T20:00:03.000Z", "9.9"),))
        path = write_segments(tmp_path / "segments.csv", (*SEGMENTS, clash))
        with pytest.raises(IngestError, match="CH_A has two samples"):
            OpssatSource(config_for(path))

    @pytest.mark.parametrize(
        ("row", "message"),
        [
            ("CH_A,2022-01-04T20:00:00,1.0", "has no time zone"),
            ("CH_A,2022-01-04T20:00:00Z,nan", "not finite"),
            ("CH_A,yesterday,1.0", ":2: Invalid isoformat"),
            ("CH_A,2022-01-04T20:00:00Z", ":2: "),
        ],
    )
    def test_malformed_row(self, tmp_path: Path, row: str, message: str) -> None:
        path = tmp_path / "segments.csv"
        path.write_text(f"channel,timestamp,value\n{row}\n", encoding="utf-8")
        with pytest.raises(IngestError, match=message):
            OpssatSource(config_for(path, channels={"CH_A": CHANNELS["CH_A"]}))

    def test_missing_column(self, tmp_path: Path) -> None:
        path = tmp_path / "segments.csv"
        path.write_text("channel,timestamp\nCH_A,2022-01-04T20:00:00Z\n", encoding="utf-8")
        with pytest.raises(IngestError, match="expected columns channel, timestamp, value"):
            OpssatSource(config_for(path))

    def test_missing_file_points_to_the_fetcher(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="make data-fetch DS=opssat"):
            OpssatSource(config_for(tmp_path / "absent.csv"))


class TestDescription:
    def test_names_the_data_version_and_every_setting_that_shapes_a_reading(
        self, opssat_config: OpssatSourceConfig, tmp_path: Path
    ) -> None:
        base = OpssatSource(opssat_config).describe()
        assert OpssatSource(opssat_config).describe() == base

        moved = write_segments(tmp_path / "elsewhere.csv")  # same bytes, another place
        assert OpssatSource(config_for(moved)).describe() == base

        edited = moved.read_text(encoding="utf-8").replace(",1.01,", ",1.99,")
        moved.write_text(edited, encoding="utf-8", newline="\n")
        assert OpssatSource(config_for(moved)).describe() != base

        other_mission = config_for(opssat_config.path, mission="other")
        assert OpssatSource(other_mission).describe() != base

        other_unit = config_for(opssat_config.path, channels=CHANNELS | {"CH_B": CHANNELS["CH_A"]})
        assert OpssatSource(other_unit).describe() != base
