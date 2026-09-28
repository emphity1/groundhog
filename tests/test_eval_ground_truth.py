# SPDX-License-Identifier: Apache-2.0
"""Labelled events from labelled segments: runs, gaps and consecutive anomalies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from groundhog.eval.ground_truth import continuous_runs, labelled_events
from groundhog.ingest.opssat import LabelledSegment

T0 = datetime(2022, 6, 1, 23, 0, tzinfo=UTC)


def segment(
    id: int, start_s: float, length_s: float, anomaly: bool, channel: str = "A", sampling: int = 5
) -> LabelledSegment:
    start = T0 + timedelta(seconds=start_s)
    return LabelledSegment(
        id=id,
        channel=channel,
        sampling_s=sampling,
        anomaly=anomaly,
        train=False,
        start=start,
        end=start + timedelta(seconds=length_s),
        samples=int(length_s // sampling) + 1,
    )


def test_consecutive_anomalous_segments_of_a_run_are_one_event() -> None:
    segments = [
        segment(10, 0, 100, False),
        segment(11, 105, 100, True),  # back to back: 5 s is one sampling period
        segment(12, 210, 100, True),
        segment(13, 315, 100, False),
        segment(14, 420, 100, True),
    ]
    events = labelled_events(segments, run_gap_periods=2)
    assert [(e.id, e.segments) for e in events] == [("A:11", (11, 12)), ("A:14", (14,))]
    assert (events[0].start, events[0].end) == (
        T0 + timedelta(seconds=105),
        T0 + timedelta(seconds=310),
    )
    assert events[0].duration_s == 205


def test_a_silence_longer_than_the_rule_breaks_the_run_and_the_event() -> None:
    segments = [
        segment(1, 0, 100, True),
        segment(2, 110, 100, True),  # 10 s = 2 periods: still the same run
        segment(3, 221, 100, True),  # 11 s > 2 periods: a new run
    ]
    runs = continuous_runs(segments, run_gap_periods=2)
    assert [[s.id for s in r.segments] for r in runs] == [[1, 2], [3]]
    assert [e.segments for e in labelled_events(segments, run_gap_periods=2)] == [(1, 2), (3,)]


def test_the_gap_rule_uses_the_sampling_of_the_later_segment() -> None:
    segments = [segment(1, 0, 100, True, sampling=5), segment(2, 103, 100, True, sampling=1)]
    assert len(labelled_events(segments, run_gap_periods=2)) == 2  # 3 s > 2 x 1 s


def test_channels_never_mix_and_events_come_in_order_of_start() -> None:
    segments = [
        segment(1, 50, 100, True, channel="B"),
        segment(2, 0, 100, True, channel="A"),
        segment(3, 105, 100, True, channel="A"),
    ]
    events = labelled_events(segments, run_gap_periods=2)
    assert [(e.channel, e.segments) for e in events] == [("A", (2, 3)), ("B", (1,))]
    assert [r.channel for r in continuous_runs(segments, run_gap_periods=2)] == ["A", "B"]
