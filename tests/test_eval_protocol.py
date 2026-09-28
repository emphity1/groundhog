# SPDX-License-Identifier: Apache-2.0
"""The protocol's refusals: each one stops the evaluation before anything is scored."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from groundhog.detectors.r0_limits import LimitsConfig
from groundhog.eval.ground_truth import LabelledEvent
from groundhog.eval.protocol import (
    Embargo,
    ProtocolError,
    check_boundaries,
    check_data_version,
    check_limits,
    check_usable,
    embargo,
    folds,
)

MD5 = "72f109630abb933a386106897a631188"
TEST_START = datetime(2022, 6, 2, 3, 0, tzinfo=UTC)
TEST_END = datetime(2022, 6, 2, 6, 29, tzinfo=UTC)
EMBARGO = Embargo(lookback_s=300, longest_event_s=6545)
FOLD = folds([(TEST_START, TEST_END)], EMBARGO)[0]


def event(id: str, start: datetime, seconds: float, channel: str = "A") -> LabelledEvent:
    return LabelledEvent(
        id=id, channel=channel, start=start, end=start + timedelta(seconds=seconds), segments=(1,)
    )


def limits(**provenance: Any) -> LimitsConfig:
    return LimitsConfig.model_validate(
        {
            "mission": "m",
            "provenance": {"data_md5": MD5} | provenance,
            "defaults": {"hysteresis": 0.05, "persistence": {"samples": 3}, "max_gap_s": 60},
            "channels": {"A": {"check": "limits", "low": 0, "high": 1, "max_rate": None}},
        }
    )


class TestEmbargo:
    def test_the_longest_lookback_plus_the_longest_event(self) -> None:
        events = [event("a", TEST_START, 100), event("b", TEST_START, 6545)]
        computed = embargo([120, 300], events)
        assert (computed.lookback_s, computed.longest_event_s, computed.seconds) == (
            300,
            6545,
            6845,
        )

    def test_each_fold_trains_on_everything_before_its_test_block_minus_the_embargo(
        self,
    ) -> None:
        later = TEST_END + timedelta(hours=4)
        first, second = folds([(TEST_START, TEST_END), (TEST_END, later)], EMBARGO)
        assert (first.number, second.number) == (1, 2)
        assert first.training_end == TEST_START - timedelta(seconds=6845)
        assert second.training_end == TEST_END - timedelta(seconds=6845)
        assert second.training.start is None and second.test.end == later


class TestDataVersion:
    def lock(self, tmp_path: Path, checksum: str = f"md5:{MD5}") -> Path:
        path = tmp_path / "datasets.lock.json"
        entry = {"file": "segments.csv", "checksum": checksum}
        path.write_text(json.dumps({"opssat": {"files": [entry]}}), encoding="utf-8")
        return path

    def test_the_version_recorded_when_fetched_passes(self, tmp_path: Path) -> None:
        check_data_version(self.lock(tmp_path), "opssat", "segments.csv", MD5)

    def test_another_version_stops_and_prints_both_hashes(self, tmp_path: Path) -> None:
        other = "0" * 32
        with pytest.raises(ProtocolError) as refused:
            check_data_version(self.lock(tmp_path), "opssat", "segments.csv", other)
        assert f"expected md5 {MD5}" in str(refused.value)
        assert f"found md5 {other}" in str(refused.value)

    def test_no_lock_no_evaluation(self, tmp_path: Path) -> None:
        with pytest.raises(ProtocolError, match="make data-fetch DS=opssat"):
            check_data_version(tmp_path / "absent.json", "opssat", "segments.csv", MD5)
        with pytest.raises(ProtocolError, match="no checksum"):
            check_data_version(self.lock(tmp_path), "opssat", "dataset.csv", MD5)
        with pytest.raises(ProtocolError, match="not md5"):
            check_data_version(self.lock(tmp_path, "sha256:ab"), "opssat", "segments.csv", MD5)


class TestLimitsProvenance:
    """The limits file says what it was fitted on; the harness holds it to that."""

    def test_limits_fitted_on_the_folds_training_data_are_accepted(self) -> None:
        check_limits(limits(training={"start": None, "end": FOLD.training_end}), MD5, FOLD)
        early = FOLD.training_end - timedelta(days=30)
        check_limits(limits(training={"start": None, "end": early}), MD5, FOLD)

    @pytest.mark.parametrize(
        ("start", "end"),
        [
            (None, FOLD.training_end + timedelta(seconds=1)),  # one second into the embargo
            (None, TEST_START + timedelta(hours=1)),  # into the test block
            (TEST_START, TEST_END),  # the test block itself
            (None, None),  # everything
            (TEST_END, None),  # after the test block: the future
        ],
    )
    def test_limits_fitted_on_the_test_block_or_the_embargo_are_refused(
        self, start: datetime | None, end: datetime | None
    ) -> None:
        with pytest.raises(ProtocolError, match="fold 1: the limits were fitted on"):
            check_limits(limits(training={"start": start, "end": end}), MD5, FOLD)

    def test_limits_fitted_on_another_data_version_are_refused(self) -> None:
        stale = limits(training={"start": None, "end": FOLD.training_end}, data_md5="1" * 32)
        with pytest.raises(ProtocolError, match=f"expected md5 {MD5}, found md5 1111"):
            check_limits(stale, MD5, FOLD)

    def test_limits_that_do_not_say_what_they_were_fitted_on_are_refused(self) -> None:
        with pytest.raises(ProtocolError, match="no training window"):
            check_limits(limits(), MD5, FOLD)


class TestBoundaries:
    def test_an_event_inside_a_block_or_starting_on_its_boundary_is_whole(self) -> None:
        check_boundaries(
            [FOLD], [event("in", TEST_START, 600), event("before", FOLD.training_end, 60)]
        )

    @pytest.mark.parametrize(
        "cut",
        [
            event("across the start", TEST_START - timedelta(seconds=60), 120),
            event("across the end", TEST_END - timedelta(seconds=60), 120),
            event("ending on the end", TEST_END - timedelta(seconds=60), 60),
        ],
    )
    def test_an_event_cut_by_a_boundary_is_refused(self, cut: LabelledEvent) -> None:
        with pytest.raises(ProtocolError, match=cut.id):
            check_boundaries([FOLD], [cut])


class TestUsable:
    def test_a_fold_needs_enough_events(self) -> None:
        few = [event(str(i), TEST_START, 60) for i in range(9)]
        with pytest.raises(ProtocolError, match="9 labelled events"):
            check_usable(FOLD, few, ["A"], {"A": [1.0]}, min_events=10)
        check_usable(FOLD, [*few, event("x", TEST_START, 60)], ["A"], {"A": [1.0]}, 10)

    def test_every_channel_tested_needs_nominal_training_data(self) -> None:
        events = [event(str(i), TEST_START, 60) for i in range(10)]
        with pytest.raises(ProtocolError, match="no nominal training data for B, C"):
            check_usable(FOLD, events, ["A", "B", "C"], {"A": [1.0], "B": []}, min_events=10)
